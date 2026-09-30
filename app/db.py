"""Хранилище: SQLite через aiosqlite.

Комнаты и сообщения сохраняются на диск, живое состояние плеера держится
в памяти (app.rooms) и сбрасывается в SQLite дебаунсом.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id            TEXT PRIMARY KEY,
    title         TEXT    NOT NULL DEFAULT '',
    created_at    REAL    NOT NULL,
    host_id       TEXT    NOT NULL DEFAULT '',
    source_json   TEXT,
    source_status TEXT    NOT NULL DEFAULT 'empty',
    source_error  TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'paused',
    position      REAL    NOT NULL DEFAULT 0,
    anchor        REAL    NOT NULL DEFAULT 0,
    rate          REAL    NOT NULL DEFAULT 1,
    seq           INTEGER NOT NULL DEFAULT 0,
    auto_pause    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id   TEXT    NOT NULL,
    kind      TEXT    NOT NULL DEFAULT 'chat',
    author    TEXT    NOT NULL DEFAULT '',
    author_id TEXT    NOT NULL DEFAULT '',
    text      TEXT    NOT NULL,
    ts        REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_room ON messages (room_id, id);
"""


class Database:
    def __init__(self, path: str | Path = config.DB_PATH) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не вызван")
        return self._conn

    @asynccontextmanager
    async def _tx(self):
        async with self._lock:
            yield self.conn
            await self.conn.commit()

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        async with self._lock, self.conn.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        async with self._lock, self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def save_room(self, room: RoomLike) -> None:
        await self.conn.execute(
            """
            INSERT INTO rooms (id, title, created_at, host_id, source_json, source_status,
                               source_error, status, position, anchor, rate, seq, auto_pause)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, host_id=excluded.host_id,
                source_json=excluded.source_json, source_status=excluded.source_status,
                source_error=excluded.source_error, status=excluded.status,
                position=excluded.position, anchor=excluded.anchor, rate=excluded.rate,
                seq=excluded.seq, auto_pause=excluded.auto_pause
            """,
            room.to_row(),
        )
        await self.conn.commit()

    async def delete_room(self, room_id: str) -> None:
        await self.conn.execute("DELETE FROM messages WHERE room_id = ?", (room_id,))
        await self.conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await self.conn.commit()

    async def load_rooms(self) -> list[dict[str, Any]]:
        return await self.fetch_all("SELECT * FROM rooms ORDER BY created_at")

    async def add_message(
        self, room_id: str, text: str, author: str, author_id: str, kind: str = "chat"
    ) -> dict[str, Any]:
        ts = time.time()
        cur = await self.conn.execute(
            "INSERT INTO messages (room_id, kind, author, author_id, text, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (room_id, kind, author, author_id, text, ts),
        )
        await self.conn.commit()
        return {
            "id": cur.lastrowid,
            "room_id": room_id,
            "kind": kind,
            "author": author,
            "author_id": author_id,
            "text": text,
            "ts": ts,
        }

    async def recent_messages(self, room_id: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.fetch_all(
            "SELECT * FROM messages WHERE room_id = ? ORDER BY id DESC LIMIT ?",
            (room_id, limit),
        )
        return list(reversed(rows))

    async def trim_messages(self, room_id: str, keep: int = 1000) -> None:
        await self.conn.execute(
            "DELETE FROM messages WHERE room_id = ? AND id NOT IN "
            "(SELECT id FROM messages WHERE room_id = ? ORDER BY id DESC LIMIT ?)",
            (room_id, room_id, keep),
        )
        await self.conn.commit()

    async def room_summaries(self) -> list[dict[str, Any]]:
        rows = await self.fetch_all(
            """
            SELECT r.id, r.title, r.created_at, r.status, r.source_status, r.source_json,
                   (SELECT COUNT(*) FROM messages m
                     WHERE m.room_id = r.id AND m.kind = 'chat') AS messages
            FROM rooms r ORDER BY r.created_at
            """
        )
        for row in rows:
            raw = row.pop("source_json", None)
            row["source_title"] = ""
            if raw:
                try:
                    row["source_title"] = json.loads(raw).get("title", "")
                except (ValueError, TypeError):
                    row["source_title"] = ""
        return rows


RoomLike = Any