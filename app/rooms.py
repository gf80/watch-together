"""Ядро: комнаты, состояние плеера, синхронизация, чат, presence.

Модель синхронизации: сервер — источник истины. Комната хранит
(status, position, anchor, rate, seq), где position — позиция в момент
серверного времени anchor. Ожидаемая позиция:

    playing: position + (server_now - anchor) * rate
    paused:  position

Клиент каждые пол-секунды сравнивает video.currentTime с ожидаемой
позицией: расхождение больше SYNC_SOFT_SKEW_SEC исправляется скоростью
воспроизведения, больше SYNC_HARD_SKEW_SEC — принудительным seek.
Сервер также сам присылает pb.sync, если дрейф клиента превысил порог.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import WebSocket

from . import config

ROOM_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_room_id() -> str:
    return "".join(random.choice(ROOM_ID_ALPHABET) for _ in range(config.ROOM_ID_LENGTH))


async def send_json(ws: WebSocket | None, payload: dict[str, Any]) -> bool:
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


@dataclass
class User:
    id: str
    name: str
    ws: WebSocket | None = None
    position: float = 0.0
    playing: bool = False
    ready: bool = False
    buffering: bool = False
    drift: float | None = None
    joined_at: float = field(default_factory=time.time)

    @property
    def online(self) -> bool:
        return self.ws is not None


@dataclass
class Source:
    kind: str = "direct"
    origin: str = "link"
    input_url: str = ""
    ref: str = ""
    base: str = ""
    title: str = ""
    hls: bool = False
    bypass: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "video/mp4"
    duration: float | None = None
    local: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    version: int = 1

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @staticmethod
    def from_json(raw: str | None) -> Source | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return Source(**{k: v for k, v in data.items() if k in Source.__dataclass_fields__})


@dataclass
class Room:
    id: str
    title: str = ""
    created_at: float = field(default_factory=time.time)
    host_id: str = ""
    source: Source | None = None
    source_status: str = "empty"
    source_error: str = ""
    status: str = "paused"
    position: float = 0.0
    anchor: float = field(default_factory=time.time)
    rate: float = 1.0
    seq: int = 0
    auto_pause: bool = config.AUTO_PAUSE_ON_BUFFER
    paused_by: str = "load"
    buffering_user: str | None = None
    users: dict[str, User] = field(default_factory=dict)
    _dirty: bool = False
    _persist_task: asyncio.Task | None = None
    _last_peers: float = 0.0

    def to_row(self) -> tuple:
        return (
            self.id,
            self.title,
            self.created_at,
            self.host_id,
            self.source.to_json() if self.source else None,
            self.source_status,
            self.source_error,
            self.status,
            self.position,
            self.anchor,
            self.rate,
            self.seq,
            1 if self.auto_pause else 0,
        )

    def expected_position(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        if self.status == "playing":
            return max(0.0, self.position + (now - self.anchor) * self.rate)
        return max(0.0, self.position)

    def stream_url(self) -> str:
        src = self.source
        if src is None:
            return ""
        if src.bypass and not src.local:
            return src.input_url or src.ref
        return f"/api/stream/{self.id}/{quote(src.ref, safe='')}"

    def source_payload(self) -> dict[str, Any] | None:
        src = self.source
        if src is None:
            return None
        return {
            "version": src.version,
            "kind": src.kind,
            "origin": src.origin,
            "title": src.title,
            "hls": src.hls,
            "url": self.stream_url(),
            "input_url": src.input_url,
            "content_type": src.content_type,
            "duration": src.duration,
            "local": src.local,
        }

    def state_payload(self, msg_type: str = "pb.state") -> dict[str, Any]:
        return {
            "type": msg_type,
            "room_id": self.id,
            "title": self.title,
            "host_id": self.host_id,
            "status": self.status,
            "position": round(self.position, 3),
            "anchor": self.anchor,
            "rate": self.rate,
            "seq": self.seq,
            "target": round(self.expected_position(), 3),
            "server_time": time.time(),
            "auto_pause": self.auto_pause,
            "paused_by": self.paused_by,
            "source_status": self.source_status,
            "source_error": self.source_error,
            "source": self.source_payload(),
            "hard_skew": config.SYNC_HARD_SKEW_SEC,
            "soft_skew": config.SYNC_SOFT_SKEW_SEC,
            "rate_min": config.SYNC_RATE_MIN,
            "rate_max": config.SYNC_RATE_MAX,
        }

    def peers_payload(self) -> dict[str, Any]:
        return {
            "type": "peers",
            "room_id": self.id,
            "server_time": time.time(),
            "users": [
                {
                    "id": u.id,
                    "name": u.name,
                    "position": round(u.position, 2),
                    "playing": u.playing,
                    "ready": u.ready,
                    "buffering": u.buffering,
                    "drift": u.drift,
                    "host": u.id == self.host_id,
                }
                for u in self.users.values()
            ],
        }


class RoomManager:
    def __init__(self, db) -> None:
        self.db = db
        self.rooms: dict[str, Room] = {}
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def load(self) -> None:
        for row in await self.db.load_rooms():
            source = Source.from_json(row.get("source_json"))
            room = Room(
                id=row["id"],
                title=row["title"] or "",
                created_at=row["created_at"],
                host_id=row["host_id"] or "",
                source=source,
                source_status=row["source_status"] or ("ready" if source else "empty"),
                source_error=row["source_error"] or "",
                status="paused",
                position=float(row["position"] or 0.0),
                rate=1.0,
                seq=int(row["seq"] or 0),
                auto_pause=True,
                paused_by="load",
            )
            room.anchor = time.time()
            self.rooms[room.id] = room

    def find(self, room_id: str) -> Room | None:
        return self.rooms.get(room_id.lower())

    async def create(self, title: str, host_id: str) -> Room:
        for _ in range(20):
            room_id = new_room_id()
            if room_id not in self.rooms:
                break
        else:
            raise RuntimeError("не удалось сгенерировать id комнаты")
        room = Room(id=room_id, title=(title or "").strip()[:80], host_id=host_id)
        self.rooms[room.id] = room
        await self.db.save_room(room)
        return room

    async def delete(self, room_id: str, notify: bool = True) -> None:
        room = self.rooms.pop(room_id, None)
        if room is None:
            return
        if room._persist_task is not None:
            room._persist_task.cancel()
        users = list(room.users.values())
        room.users.clear()
        if notify and users:
            await asyncio.gather(
                *(send_json(u.ws, {"type": "room.closed", "room_id": room.id}) for u in users),
                return_exceptions=True,
            )
        for user in users:
            if user.ws is not None:
                with contextlib.suppress(Exception):
                    await user.ws.close(code=4001, reason="комната удалена")
        await self.db.delete_room(room_id)

    async def sweep(self) -> None:
        now = time.time()
        for room_id, room in list(self.rooms.items()):
            if room.users or now - room.created_at < config.ROOM_IDLE_TTL:
                continue
            await self.delete(room_id)

    def schedule_persist(self, room: Room) -> None:
        room._dirty = True
        if room._persist_task is not None and not room._persist_task.done():
            return

        async def _worker() -> None:
            try:
                while room._dirty:
                    room._dirty = False
                    await asyncio.sleep(0.5)
                    await self.db.save_room(room)
            except asyncio.CancelledError:
                raise
            except Exception:
                room._dirty = False

        room._persist_task = asyncio.create_task(_worker())

    async def persist_now(self, room: Room) -> None:
        await self.db.save_room(room)

    async def broadcast(self, room: Room, payload: dict[str, Any], exclude: str | None = None) -> None:
        targets = [u for u in room.users.values() if u.ws is not None and u.id != exclude]
        if not targets:
            return
        results = await asyncio.gather(*(send_json(u.ws, payload) for u in targets))
        for user, ok in zip(targets, results, strict=True):
            if not ok:
                self.drop_user(room, user.id)

    def drop_user(self, room: Room, user_id: str) -> None:
        room.users.pop(user_id, None)

    def add_user(self, room: Room, user_id: str, name: str, ws: WebSocket) -> tuple[User, bool]:
        existing = room.users.get(user_id)
        if existing is None:
            user = User(id=user_id, name=name, ws=ws)
            room.users[user_id] = user
            return user, True
        existing.name = name
        existing.ws = ws
        return existing, False

    async def announce(self, room: Room, text: str) -> None:
        message = await self.db.add_message(room.id, text, author="", author_id="", kind="system")
        await self.db.trim_messages(room.id)
        await self.broadcast(room, {"type": "chat.msg", **message})

    async def remove_user(self, room: Room, user: User) -> None:
        current = room.users.get(user.id)
        if current is None or current.ws is not user.ws:
            return
        del room.users[user.id]
        if room.buffering_user == user.id:
            room.buffering_user = None
        if room.id not in self.rooms:
            return
        await self.broadcast(room, room.peers_payload())
        if not room.users:
            await self.announce(room, "Комната опустела. Можно закрывать вкладку.")
        await self.schedule_broadcast_peers(room)

    async def add_message(self, room: Room, user: User, text: str) -> dict[str, Any] | None:
        text = text.strip()
        if not text:
            return None
        message = await self.db.add_message(
            room.id, text[: config.MAX_MESSAGE_LENGTH], user.name, user.id, kind="chat"
        )
        await self.db.trim_messages(room.id)
        await self.broadcast(room, {"type": "chat.msg", **message})
        return message

    async def schedule_broadcast_peers(self, room: Room, force: bool = False) -> None:
        now = time.time()
        if not force and now - room._last_peers < 1.0:
            return
        room._last_peers = now
        await self.broadcast(room, room.peers_payload())

    async def apply_control(
        self,
        room: Room,
        action: str,
        position: float | None = None,
        rate: float | None = None,
        actor: User | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        action = action.strip().lower()
        if action == "toggle":
            action = "pause" if room.status == "playing" else "play"
        if action == "play":
            target = position if position is not None else room.expected_position(now)
            room.position = max(0.0, float(target))
            room.anchor = now
            room.status = "playing"
            room.paused_by = "user"
            room.buffering_user = None
        elif action == "pause":
            room.position = room.expected_position(now)
            room.anchor = now
            room.status = "paused"
            room.paused_by = "user"
            room.buffering_user = None
        elif action == "seek":
            room.position = max(0.0, float(position or 0.0))
            room.anchor = now
            room.paused_by = "seek"
        else:
            raise ValueError(f"неизвестное действие: {action}")
        room.seq += 1
        payload = room.state_payload()
        await self.broadcast(room, payload)
        self.schedule_persist(room)
        if actor is not None:
            await self.schedule_broadcast_peers(room)
        return payload

    async def report(self, room: Room, user: User, data: dict[str, Any]) -> None:
        now = time.time()
        if "position" in data:
            with contextlib.suppress(TypeError, ValueError):
                user.position = max(0.0, float(data["position"]))
        if "playing" in data:
            user.playing = bool(data["playing"])
        if "ready" in data:
            user.ready = bool(data["ready"])
        if "buffering" in data:
            buffering = bool(data["buffering"])
            user.buffering = buffering
            if buffering and room.status == "playing" and room.auto_pause:
                room.position = user.position
                room.anchor = now
                room.status = "paused"
                room.paused_by = "buffer"
                room.buffering_user = user.id
                room.seq += 1
                await self.broadcast(room, room.state_payload())
                self.schedule_persist(room)
            elif (
                not buffering
                and room.paused_by == "buffer"
                and room.buffering_user == user.id
            ):
                room.position = user.position
                room.anchor = now
                room.status = "playing"
                room.paused_by = ""
                room.buffering_user = None
                room.seq += 1
                await self.broadcast(room, room.state_payload())
                self.schedule_persist(room)

        drift = user.position - room.expected_position(now)
        user.drift = round(drift, 2)
        if abs(drift) > config.SYNC_HARD_SKEW_SEC:
            await send_json(user.ws, room.state_payload("pb.sync"))
        await self.schedule_broadcast_peers(room)

    async def set_source(self, room: Room, source: Source, source_status: str = "ready") -> None:
        previous_version = room.source.version if room.source else 0
        source.version = previous_version + 1
        room.source = source
        room.source_status = source_status
        room.source_error = ""
        room.status = "paused"
        room.position = 0.0
        room.anchor = time.time()
        room.paused_by = "load"
        room.buffering_user = None
        room.seq += 1
        await self.broadcast(room, room.state_payload())
        self.schedule_persist(room)

    async def set_source_preparing(self, room: Room, message: str = "Скачиваем и собираем видео…") -> None:
        room.source_status = "preparing"
        room.source_error = message
        room.seq += 1
        await self.broadcast(room, room.state_payload())
        self.schedule_persist(room)

    async def set_source_error(self, room: Room, error: str) -> None:
        room.source_status = "error" if room.source is None else "ready"
        room.source_error = error[:500]
        room.seq += 1
        await self.broadcast(room, room.state_payload())
        self.schedule_persist(room)

    def claim_host(self, room: Room) -> bool:
        """Назначить хостом первого участника, если прежний хост офлайн."""
        if room.host_id in room.users:
            return False
        host = next(iter(room.users.values()))
        room.host_id = host.id
        return True

    def snapshot(self, room: Room, limit: int = config.HISTORY_LIMIT) -> dict[str, Any]:
        return {
            "room": room.state_payload(),
            "peers": room.peers_payload()["users"],
        }


def stream_ref_for(room: Room, ref: str) -> str:
    """Абсолютный URL апстрима: ref либо абсолютный, либо относительный к base."""
    source = room.source
    if source is None:
        return ref
    if source.local:
        return ref
    if ref.startswith(("http://", "https://")):
        return ref
    from urllib.parse import urljoin

    return urljoin(source.base, ref)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""