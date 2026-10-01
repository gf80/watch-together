"""Сквозная проверка API, WebSocket, синхронизации и прокси.

Запуск (сервер должен быть поднят на BASE_URL):
    python tests/smoke.py

Сеть не требуется: «фильм» раздаёт локальный HTTP-сервер из tests.media_server.
Проверки, которым нужен интернет, помечаются как SKIP.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any
from urllib.parse import quote

import httpx
import websockets

sys.path.insert(0, os.path.dirname(__file__))
from media_server import make_hls, make_tone, serve_media

BASE_URL = os.getenv("WT_BASE_URL", "http://127.0.0.1:8123")
WS_BASE = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
MEDIA_PORT = int(os.getenv("WT_MEDIA_PORT", "8125"))

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name} {extra}".strip())
        print(f"  FAIL {name} {extra}")


def skip(name: str, why: str) -> None:
    SKIPPED.append(name)
    print(f"  skip {name} ({why})")


class Client:
    def __init__(self, uid: str, name: str) -> None:
        self.uid = uid
        self.name = name
        self.ws: Any = None
        self.messages: list[dict] = []
        self._task: asyncio.Task | None = None

    async def connect(self, room_id: str) -> None:
        url = f"{WS_BASE}/ws/{room_id}?uid={self.uid}&name={self.name}"
        self.ws = await websockets.connect(url, open_timeout=10)
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            async for raw in self.ws:
                self.messages.append(json.loads(raw))
        except Exception:
            pass

    async def send(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload))

    async def wait_for(self, msg_type: str, timeout: float = 5.0, **match: Any) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for message in self.messages:
                if message.get("type") != msg_type:
                    continue
                if all(message.get(key) == value for key, value in match.items()):
                    return message
            await asyncio.sleep(0.05)
        raise TimeoutError(f"нет сообщения {msg_type} {match} за {timeout}s")

    async def wait_until(self, msg_type: str, predicate, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for message in self.messages:
                if message.get("type") == msg_type and predicate(message):
                    return message
            await asyncio.sleep(0.05)
        raise TimeoutError(f"нет сообщения {msg_type} с нужным содержимым за {timeout}s")

    def latest(self, msg_type: str) -> dict | None:
        found = [m for m in self.messages if m.get("type") == msg_type]
        return found[-1] if found else None

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self._task is not None:
            await asyncio.sleep(0.1)
            self._task.cancel()


async def main() -> int:
    media_dir = os.path.join(os.path.dirname(__file__), "_media")
    os.makedirs(media_dir, exist_ok=True)
    make_tone(os.path.join(media_dir, "tone.wav"))
    server = serve_media(media_dir, MEDIA_PORT)
    local_media = f"http://127.0.0.1:{MEDIA_PORT}/tone.wav"
    hls_media = make_hls(media_dir, MEDIA_PORT)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as http:
        print("\n== служебные endpoints ==")
        health = await http.get("/api/health")
        check("GET /api/health", health.status_code == 200 and health.json()["ok"])
        caps = (await http.get("/api/capabilities")).json()
        check(
            "GET /api/capabilities",
            "yt_dlp" in caps and "allow_private" in caps,
            str(caps),
        )
        allow_private = bool(caps.get("allow_private"))

        print("\n== комнаты ==")
        created = await http.post(
            "/api/rooms", json={"title": "Смоук-тест"}, headers={"X-User-Id": "smoke-host"}
        )
        check("POST /api/rooms", created.status_code == 201, created.text)
        room_id = created.json()["id"]
        guest_headers = {"X-User-Id": "smoke-guest"}

        listed = await http.get("/api/rooms", headers=guest_headers)
        check(
            "комната видна в списке",
            any(r["id"] == room_id for r in listed.json()["rooms"]),
        )

        session = await http.post(
            "/api/session", json={"uid": "smoke-guest", "name": "Гость Тест"}
        )
        check("POST /api/session с кириллицей", session.status_code == 200, session.text)
        cookie_ok = any(
            "wt_uid=smoke-guest" in value for key, value in session.headers.items() if key == "set-cookie"
        )
        check("cookie с uid установлена", cookie_ok, str(session.headers.get_list("set-cookie")))

        denied = await http.post(
            f"/api/rooms/{room_id}/source",
            json={"url": "https://example.com/a.mp4"},
            headers=guest_headers,
        )
        check("гость не может задать источник", denied.status_code == 403, denied.text)

        print("\n== WebSocket ==")
        host = Client("smoke-host", "Хост")
        guest = Client("smoke-guest", "Гость")
        await host.connect(room_id)
        welcome = await host.wait_for("welcome")
        check("welcome содержит состояние плеера", welcome["room"]["status"] == "paused")
        check("welcome: хост определён", welcome["you"]["host"] is True)

        await guest.connect(room_id)
        await guest.wait_for("welcome")
        peers = await host.wait_until("peers", lambda m: len(m["users"]) == 2)
        check("хост видит второго участника", len(peers["users"]) == 2)
        join_msg = await host.wait_for("chat.msg", kind="system")
        check("системное сообщение о входе", "присоединился" in join_msg["text"])

        print("\n== чат ==")
        await guest.send({"type": "chat", "text": "Привет, всем!"})
        got = await host.wait_for("chat.msg", text="Привет, всем!")
        check("сообщение доставлено", got["author"] == "Гость")
        history = await http.get(f"/api/rooms/{room_id}/messages")
        check(
            "сообщение в истории SQLite",
            any(m["text"] == "Привет, всем!" for m in history.json()["messages"]),
        )

        print("\n== прочитано ==")
        await guest.send({"type": "chat.seen", "id": got["id"]})
        seen = await host.wait_for("chat.seen")
        check("автор получил отметку о прочтении", seen["id"] == got["id"], str(seen))
        check("в отметке есть id читателя", bool(seen.get("user_id")), str(seen))
        # Повторная отметка не должна переписывать время.
        first_seen = seen["seen_at"]
        await guest.send({"type": "chat.seen", "id": got["id"]})
        again = await host.wait_until(
            "chat.seen", lambda m: m["id"] == got["id"] and m["seen_at"] == first_seen, 3.0
        )
        check("повторная отметка не меняет время", bool(again))
        # Мусорные и чужие id не должны ронять комнату и не должны вещать отметку.
        await guest.send({"type": "chat.seen", "id": "не число"})
        await guest.send({"type": "chat.seen", "id": -1})
        await guest.send({"type": "chat.seen", "id": 999999})
        await guest.send({"type": "chat", "text": "всё ещё работает"})
        survived = await host.wait_for("chat.msg", text="всё ещё работает")
        check("битые id не ломают комнату", bool(survived))
        # Сообщение из чужой комнаты отмечать нельзя: id живёт в общей базе.
        other = await http.post("/api/rooms", json={"nickname": "Посторонний"})
        other_id = other.json()["id"]
        outsider = Client("smoke-outsider", "Посторонний")
        await outsider.connect(other_id)
        await outsider.wait_for("welcome")
        await outsider.send({"type": "chat", "text": "секретное"})
        other_msg = await outsider.wait_for("chat.msg", text="секретное")
        before = len([m for m in host.messages if m.get("type") == "chat.seen"])
        await guest.send({"type": "chat.seen", "id": other_msg["id"]})
        await asyncio.sleep(0.5)
        after = [m for m in host.messages if m.get("type") == "chat.seen"]
        check(
            "чужое сообщение не отмечено прочитанным",
            len(after) == before and not any(m["id"] == other_msg["id"] for m in after),
            str(after),
        )
        await outsider.close()

        print("\n== синхронизация ==")
        await host.send({"type": "pb.control", "action": "play", "position": 120.5})
        state = await guest.wait_for("pb.state")
        check("пуск синхронизирован", state["status"] == "playing", str(state))
        check("позиция передана", abs(state["position"] - 120.5) < 0.05, str(state["position"]))

        await guest.send({"type": "pb.control", "action": "toggle"})
        await asyncio.sleep(0.4)
        paused = await host.wait_until("pb.state", lambda m: m["status"] == "paused")
        check("общая пауза от гостя", paused["status"] == "paused")
        check(
            "позиция зафиксировалась на ~120.5",
            abs(paused["position"] - 120.5) < 1.5,
            str(paused["position"]),
        )

        await host.send({"type": "pb.control", "action": "seek", "position": 42.0})
        seeked = await guest.wait_until("pb.state", lambda m: abs(m["position"] - 42.0) < 0.01)
        check("перемотка для всех", abs(seeked["position"] - 42.0) < 0.01)

        await host.send({"type": "pb.control", "action": "rate", "rate": 1.5})
        rate_err = await host.wait_until("error", lambda m: "rate" in m.get("message", ""), 2.0)
        check("смена скорости запрещена на сервере", "rate" in rate_err["message"])
        latest = host.latest("pb.state")
        check("скорость осталась 1.0", latest is None or latest["rate"] == 1.0)

        await host.send({"type": "pb.control", "action": "autopause", "position": 0})
        ap_err = await host.wait_until(
            "error", lambda m: "autopause" in m.get("message", ""), 2.0
        )
        check("отключение авто-паузы запрещено на сервере", "autopause" in ap_err["message"])
        await asyncio.sleep(0.3)
        await host.send({"type": "pb.control", "action": "play"})
        await asyncio.sleep(0.3)
        await host.send({"type": "pb.report", "position": 3.0, "playing": True})
        sync = await host.wait_for("pb.sync", timeout=3.0)
        check("сервер ловит сильный дрейф и шлёт pb.sync", sync["type"] == "pb.sync")
        check("pb.sync содержит цель", "target" in sync)

        await guest.send({"type": "pb.report", "position": 0.5, "playing": True})
        guest_sync = await guest.wait_for("pb.sync", timeout=3.0)
        check(
            "pb.sync уходит именно отставшему клиенту",
            guest_sync["type"] == "pb.sync" and abs(guest_sync["target"] - sync["target"]) < 0.5,
            f"{guest_sync['target']} против {sync['target']}",
        )

        await guest.send({"type": "pb.report", "position": 100.0, "buffering": True})
        await asyncio.sleep(0.5)
        buffered = host.latest("pb.state")
        check(
            "общая пауза при буферизации",
            buffered["status"] == "paused" and buffered["paused_by"] == "buffer",
            str(buffered.get("paused_by")),
        )
        await guest.send({"type": "pb.report", "position": 100.0, "buffering": False})
        await asyncio.sleep(0.5)
        resumed = host.latest("pb.state")
        check("автовозобновление после буфера", resumed["status"] == "playing")

        await guest.send({"type": "ping", "t": 12345})
        pong = await guest.wait_for("pong")
        check("ping/pong для синхронизации часов", pong["t"] == 12345 and pong["server_time"] > 0)

        print("\n== переподключение ==")
        await guest.close()
        await asyncio.sleep(0.6)
        guest.messages.clear()
        await guest.connect(room_id)
        again = await guest.wait_for("welcome")
        check("история приходит при повторном входе", len(again["history"]) >= 2, str(len(again["history"])))
        check("состояние плеера переживает переподключение", "target" in again["room"])

        print("\n== источник видео ==")
        bad = await http.post(
            f"/api/rooms/{room_id}/source",
            json={"url": "ftp://example.com/x.mp4"},
            headers={"X-User-Id": "smoke-host"},
        )
        check("некорректная схема отклоняется", bad.status_code == 400, bad.text)

        media_url = local_media if allow_private else "https://example.com/movie.mp4"
        if allow_private:
            source = await http.post(
                f"/api/rooms/{room_id}/source",
                json={"url": media_url, "title": "Тестовый трек"},
                headers={"X-User-Id": "smoke-host"},
            )
            check("прямая ссылка принята", source.status_code == 200, source.text)
            if source.status_code == 200:
                payload = source.json()
                check("источник не HLS", payload["source"]["hls"] is False)
                check(
                    "клиент получает ссылку на прокси",
                    payload["source"]["url"].startswith(f"/api/stream/{room_id}/"),
                )
                stream = await http.get(
                    payload["source"]["url"],
                    headers={"X-User-Id": "smoke-host", "Range": "bytes=0-1023"},
                )
                check(
                    "прокси отдаёт Range-ответ (206)",
                    stream.status_code == 206
                    and len(stream.content) == 1024
                    and stream.headers.get("content-range", "").startswith("bytes 0-1023/"),
                    f"{stream.status_code} {stream.headers.get('content-range')}",
                )
                full = await http.get(payload["source"]["url"], headers={"X-User-Id": "smoke-host"})
                check(
                    "прокси отдаёт файл целиком (200, accept-ranges)",
                    full.status_code == 200
                    and full.headers.get("accept-ranges") == "bytes"
                    and full.headers.get("content-type") == "audio/wav",
                    f"{full.status_code} {dict(full.headers)}",
                )
                check(
                    "прокси не дублирует content-type",
                    len(full.headers.get_list("content-type")) == 1,
                    str(full.headers.get_list("content-type")),
                )
                async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as stranger:
                    anon = await stranger.get(payload["source"]["url"])
                check(
                    "прокси закрыт для чужих (без сессии)",
                    anon.status_code == 403,
                    f"{anon.status_code} {anon.text[:120]}",
                )
                outsider = await http.get(
                    payload["source"]["url"], headers={"X-User-Id": "smoke-outsider"}
                )
                check(
                    "прокси закрыт для участника другой комнаты",
                    outsider.status_code in (403, 404),
                    f"{outsider.status_code} {outsider.text[:120]}",
                )

                print("\n== HLS ==")
                hls_source = await http.post(
                    f"/api/rooms/{room_id}/source",
                    json={"url": hls_media, "title": "HLS поток"},
                    headers={"X-User-Id": "smoke-host"},
                )
                check("HLS-ссылка принята", hls_source.status_code == 200, hls_source.text)
                if hls_source.status_code == 200:
                    hls = hls_source.json()["source"]
                    check("источник помечен как HLS", hls["hls"] is True)
                    check(
                        "тип плейлиста — HLS",
                        hls["content_type"] == "application/vnd.apple.mpegurl",
                        hls["content_type"],
                    )
                    manifest = await http.get(hls["url"], headers={"X-User-Id": "smoke-host"})
                    text = manifest.text
                    check("манифест отдан", manifest.status_code == 200, str(manifest.status_code))
                    check(
                        "относительный сегмент остаётся относительным",
                        "segment000.ts" in manifest.text.splitlines(),
                        repr(manifest.text.splitlines()[:6]),
                    )
                    absolute_segment = hls_media.replace(
                        "/hls/index.m3u8", "/hls/segment001.ts"
                    )
                    check(
                        "абсолютный URL из манифеста переписан на прокси",
                        f"/api/stream/{room_id}/{quote(absolute_segment, safe='')}" in text,
                        text[:300],
                    )
                    rel = await http.get(
                        f"/api/stream/{room_id}/segment000.ts",
                        headers={"X-User-Id": "smoke-host", "Range": "bytes=0-99"},
                    )
                    check(
                        "относительный сегмент отдаётся с Range",
                        rel.status_code == 206 and len(rel.content) == 100,
                        f"{rel.status_code} {len(rel.content)}",
                    )
                    absolute = f"/api/stream/{room_id}/{quote(absolute_segment, safe='')}"
                    seg = await http.get(
                        absolute, headers={"X-User-Id": "smoke-host", "Range": "bytes=0-49"}
                    )
                    check(
                        "хост из манифеста попал в allowlist комнаты",
                        seg.status_code == 206 and len(seg.content) == 50,
                        f"{seg.status_code} {len(seg.content)}",
                    )
        else:
            skip("прокси: Range и целостность потока", "нужен WT_ALLOW_PRIVATE_PROXY=1")
            local = await http.post(
                f"/api/rooms/{room_id}/source",
                json={"url": local_media},
                headers={"X-User-Id": "smoke-host"},
            )
            check(
                "локальный адрес отклоняется (SSRF)",
                local.status_code == 400 and "локальным" in local.text,
                local.text,
            )

        print("\n== удаление комнаты ==")
        blocked = await http.delete(f"/api/rooms/{room_id}", headers={"X-User-Id": "smoke-guest"})
        check("чужой не может удалить комнату", blocked.status_code == 403, blocked.text)

        guest_closed = asyncio.ensure_future(guest.wait_for("room.closed", room_id=room_id))
        await asyncio.sleep(0.2)
        deleted = await http.delete(f"/api/rooms/{room_id}", headers={"X-User-Id": "smoke-host"})
        check("хост удаляет комнату", deleted.status_code == 200, deleted.text)

        try:
            await guest_closed
            check("остальным пришло уведомление о закрытии", True)
        except Exception as err:
            check("остальным пришло уведомление о закрытии", False, str(err)[:100])

        missing = await http.get(f"/api/rooms/{room_id}")
        check("комната удалена", missing.status_code == 404, missing.text)
        listed = await http.get("/api/rooms")
        check(
            "комнаты нет в списке",
            all(r["id"] != room_id for r in listed.json()["rooms"]),
        )
        history = await http.get(f"/api/rooms/{room_id}/messages")
        check("история комнаты удалена", history.status_code == 404, history.text)
        repeat = await http.delete(f"/api/rooms/{room_id}", headers={"X-User-Id": "smoke-host"})
        check("повторное удаление даёт 404", repeat.status_code == 404, repeat.text)

        await host.close()

    server.shutdown()
    print(f"\n{'=' * 46}")
    print(f"успешно: {len(PASSED)}   провалено: {len(FAILED)}   пропущено: {len(SKIPPED)}")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))