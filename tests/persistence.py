"""Проверка, что комнаты и чат переживают перезапуск сервера.

Скрипт сам поднимает uvicorn в отдельном процессе, поэтому запускается
автономно:
    python tests/persistence.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.getenv("WT_PERSIST_PORT", "8126"))
DATA_DIR = ROOT / "data" / "_test_persist"
BASE_URL = f"http://127.0.0.1:{PORT}"
WS_BASE = BASE_URL.replace("http://", "ws://")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else f"{name} ({extra})")
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {'' if ok else extra}")


def start_server() -> subprocess.Popen:
    env = {
        **os.environ,
        "WT_DATA_DIR": str(DATA_DIR),
        "WT_ALLOW_PRIVATE_PROXY": "1",
        "PYTHONPATH": str(ROOT),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            httpx.get(f"{BASE_URL}/api/health", timeout=1.0)
            return process
        except Exception:
            time.sleep(0.4)
    process.kill()
    raise RuntimeError("сервер не поднялся")


async def wait_healthy() -> None:
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            httpx.get(f"{BASE_URL}/api/health", timeout=1.0)
            return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("сервер не поднялся")


def main() -> int:
    print(f"каталог данных: {DATA_DIR}")
    server = start_server()
    try:
        with httpx.Client(base_url=BASE_URL, timeout=20) as http:
            room = http.post(
                "/api/rooms", json={"title": "Персистентность"}, headers={"X-User-Id": "p1"}
            ).json()
            room_id = room["id"]
            print(f"комната: {room_id}")

            source = http.post(
                f"/api/rooms/{room_id}/source",
                json={
                    "url": "https://cdn.example.com/movies/film.mp4",
                    "title": "Фильм",
                },
                headers={"X-User-Id": "p1"},
            )
            check("источник задан до перезапуска", source.status_code == 200, source.text)

        async def chat() -> None:
            async with websockets.connect(
                f"{WS_BASE}/ws/{room_id}?uid=p1&name=Пользователь"
            ) as ws:
                await ws.send(json.dumps({"type": "chat", "text": "Сообщение до перезапуска"}))
                await asyncio.sleep(0.5)

        asyncio.run(chat())
        with httpx.Client(base_url=BASE_URL, timeout=20) as http:
            http.post(
                f"/api/rooms/{room_id}/control",
                json={"action": "play", "position": 123.5},
                headers={"X-User-Id": "p1"},
            )
        time.sleep(1.0)

        print("\nперезапускаем сервер...")
        server.terminate()
        server.wait(timeout=15)
        server = start_server()

        with httpx.Client(base_url=BASE_URL, timeout=20) as http:
            print("\n== после перезапуска ==")
            state = http.get(f"/api/rooms/{room_id}")
            check("комната восстановлена из SQLite", state.status_code == 200, state.text)
            if state.status_code == 200:
                payload = state.json()["room"]
                check("название сохранено", payload["title"] == "Персистентность", payload["title"])
                check("источник сохранён", (payload["source"] or {}).get("title") == "Фильм")
                check("позиция сохранена", abs(payload["position"] - 123.5) < 2.0, str(payload["position"]))
                check("после перезапуска плеер на паузе", payload["status"] == "paused", payload["status"])

            history = http.get(f"/api/rooms/{room_id}/messages").json()["messages"]
            check(
                "история чата сохранена",
                any(m["text"] == "Сообщение до перезапуска" for m in history),
                str([m["text"] for m in history]),
            )

            listed = http.get("/api/rooms").json()["rooms"]
            check("комната видна в лобби после перезапуска", any(r["id"] == room_id for r in listed))

        async def chat_again() -> None:
            async with websockets.connect(
                f"{WS_BASE}/ws/{room_id}?uid=p2&name=Второй"
            ) as ws:
                welcome = json.loads(await ws.recv())
                check(
                    "новый участник получает старую историю",
                    any(
                        m["text"] == "Сообщение до перезапуска"
                        for m in welcome.get("history", [])
                    ),
                )

        asyncio.run(chat_again())
    finally:
        server.terminate()
        server.wait(timeout=15)
        for _ in range(10):
            shutil.rmtree(DATA_DIR, ignore_errors=True)
            if not DATA_DIR.exists():
                break
            time.sleep(0.5)

    print(f"\n{'=' * 46}\nуспешно: {len(PASSED)}   провалено: {len(FAILED)}")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())