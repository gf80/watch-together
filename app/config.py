"""Настройки. Переопределяются переменными окружения с префиксом WT_."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

DATA_DIR = Path(os.getenv("WT_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = Path(os.getenv("WT_DB_PATH", str(DATA_DIR / "watch_together.sqlite3")))

ROOM_ID_LENGTH = 6
MAX_ROOMS = 500
MAX_PARTICIPANTS = 16
MAX_MESSAGE_LENGTH = 1000
HISTORY_LIMIT = 100
ROOM_IDLE_TTL = 6 * 3600

SYNC_HARD_SKEW_SEC = 1.9
SYNC_SOFT_SKEW_SEC = 0.35
SYNC_RATE_MIN = 0.92
SYNC_RATE_MAX = 1.08
AUTO_PAUSE_ON_BUFFER = True

PROXY_TIMEOUT = 20.0
PROXY_MAX_BYTES = 256 * 1024 * 1024
ALLOW_PRIVATE_PROXY = os.getenv("WT_ALLOW_PRIVATE_PROXY", "") in {"1", "true", "yes", "on"}
YT_MAX_HEIGHT = 720
YT_MAX_FILESIZE = 4 * 1024 * 1024 * 1024
REMUX_TIMEOUT = 1800
CHUNK_SIZE = 64 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)