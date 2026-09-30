"""Разбор источника видео.

Два пути:
  1) Прямая ссылка на файл или HLS-плейлист — ничего разбирать не надо.
  2) Ссылка на страницу (YouTube, Rutube, VK, ...) — yt-dlp достаёт
     готовый прогрессивный поток. Если прогрессивного нет (DASH с
     раздельными дорожками), файл собирается ffmpeg-ремьюзом в локальный
     кэш и раздаётся уже свой (это заодно даёт нормальный seek).
"""
from __future__ import annotations

import asyncio
import functools
import glob
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .media import HLS_TYPE, extension_of, looks_like_hls, looks_like_media_file, media_type_for
from .rooms import Source, host_of

try:  # pragma: no cover - зависит от окружения
    import yt_dlp
except Exception:  # pragma: no cover
    yt_dlp = None


class ResolveError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    """Ищет ffmpeg в PATH, а если там пусто — в типовых местах установки.

    Нужен из-за того, что winget и установщики часто дописывают путь только в
    реестр: уже запущенный сервер его не видит, а перезапуск терминала не
    всегда удобен.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    roots = [
        os.getenv("LOCALAPPDATA", ""),
        os.getenv("PROGRAMFILES", r"C:\Program Files"),
        os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        str(config.BASE_DIR / "bin"),
    ]
    patterns = [
        r"Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe",
        r"Microsoft\WinGet\Packages\BtbN.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe",
        r"ffmpeg\bin\ffmpeg.exe",
        r"ffmpeg*\bin\ffmpeg.exe",
    ]
    for root in filter(None, roots):
        for pattern in patterns:
            for candidate in glob.glob(str(Path(root) / pattern)):
                if Path(candidate).is_file():
                    return candidate
    return None


DNS_ERRORS = (
    "getaddrinfo failed",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
)

PRIVATE_HINT = (
    "сервер не смог разрешить домен — нет доступа в интернет или домен заблокирован. "
    "Проверьте, открывается ли ссылка в браузере на этой же машине"
)


def friendly_error(exc: Exception) -> str:
    """Читаемое сообщение вместо сырого вывода yt-dlp с ANSI-кодами."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
    text = re.sub(r"ERROR:\s*", "", text).strip()
    lowered = text.lower()

    if any(marker in lowered for marker in DNS_ERRORS):
        return f"не удалось разобрать ссылку: {PRIVATE_HINT}"
    if "private video" in lowered or "приватное" in lowered:
        return "видео приватное, нужна другая ссылка"
    if "sign in" in lowered or "members-only" in lowered or "подтвердите" in lowered:
        return "видео доступно только по подписке или авторизации"
    if "age" in lowered and "confirm" in lowered:
        return "видео с возрастным ограничением, нужен вход в аккаунт"
    if "unsupported url" in lowered:
        return "сайт не поддерживается yt-dlp, попробуйте прямую ссылку на файл или .m3u8"
    if "http error 404" in lowered or "404" in lowered:
        return "страница не найдена, проверьте ссылку"
    if "http error 403" in lowered or "403" in lowered:
        return "доступ запрещён (403): сайт требует входа или заблокировал запрос"
    return f"не удалось разобрать ссылку: {text[:200]}"


@dataclass
class ProbeResult:
    source: Source | None = None
    needs_prepare: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def direct_source(url: str, title: str = "", bypass: bool = False) -> Source:
    hls = looks_like_hls(url)
    return Source(
        kind="direct",
        origin="link",
        input_url=url,
        ref=url,
        base=url.rsplit("/", 1)[0] + "/",
        title=title or (url.rsplit("/", 1)[-1] or "Видео"),
        hls=hls,
        bypass=bypass,
        content_type=HLS_TYPE if hls else media_type_for(url),
        local=False,
        allowed_hosts=[h for h in (host_of(url),) if h],
    )


def _is_media_ext(ext: str) -> bool:
    return ext in {"mp4", "webm", "m4v", "mov", "m3u8", "mp3", "m4a", "opus", "ogg", "ogv", "flv"}


def _is_hls_format(fmt: dict[str, Any]) -> bool:
    return fmt.get("ext") == "m3u8" or (fmt.get("protocol") or "") in {"m3u8", "m3u8_native"}


def _is_muxed_candidate(fmt: dict[str, Any]) -> bool:
    """Формат, который браузер способен играть сам, без склейки.

    yt-dlp у многих сайтов (VK, Rutube) не заполняет vcodec/acodec, даже
    отдавая готовый mp4 с видео и звуком. Если отбросить такие форматы,
    сервер начнёт качать фильм целиком и склеивать ffmpeg, хотя можно
    отдать поток через прокси сразу.
    """
    if not fmt.get("url"):
        return False
    if (fmt.get("protocol") or "") not in {"https", "http"}:
        return False
    if fmt.get("ext") not in {"mp4", "webm", "m4v", "mov"}:
        return False
    vcodec, acodec = fmt.get("vcodec"), fmt.get("acodec")
    return not (vcodec == "none" or acodec == "none")


def _is_hls_muxed(fmt: dict[str, Any]) -> bool:
    if not fmt.get("url") or not _is_hls_format(fmt):
        return False
    return fmt.get("vcodec") != "none" and fmt.get("acodec") != "none"


def _pick_format(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    muxed = [f for f in formats if _is_muxed_candidate(f)]
    hls = [f for f in formats if _is_hls_muxed(f)]

    def best(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not pool:
            return None
        allowed = [f for f in pool if (f.get("height") or 0) <= config.YT_MAX_HEIGHT]
        chosen = allowed or pool
        return max(
            chosen,
            key=lambda f: (
                f.get("height") or 0,
                f.get("tbr") or 0,
                1 if f.get("ext") == "mp4" else 0,
            ),
        )

    # Прогрессивный файл надёжнее (обычный seek по Range), HLS — запасной
    # вариант, когда отдельного mp4 нет.
    return best(muxed) or best(hls)


def _source_from_format(info: dict[str, Any], fmt: dict[str, Any], input_url: str) -> Source:
    url = fmt["url"]
    hls = _is_hls_format(fmt)
    return Source(
        kind="resolved",
        origin="page",
        input_url=input_url,
        ref=url,
        base=url.rsplit("/", 1)[0] + "/",
        title=info.get("title") or "Видео",
        hls=hls,
        content_type=HLS_TYPE if hls else f"video/{fmt.get('ext') or 'mp4'}",
        duration=info.get("duration"),
        allowed_hosts=[h for h in (host_of(url), host_of(info.get("webpage_url") or "")) if h],
        headers={k: v for k, v in (info.get("http_headers") or {}).items() if isinstance(v, str)},
    )


def probe_sync(url: str, title: str = "", bypass: bool = False) -> ProbeResult:
    if looks_like_hls(url) or looks_like_media_file(url):
        return ProbeResult(source=direct_source(url, title, bypass))

    if yt_dlp is None:
        return ProbeResult(
            source=direct_source(url, title, bypass),
            note="yt-dlp не установлен: ссылка отдаётся как есть",
        )

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "http_headers": {"User-Agent": config.USER_AGENT},
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise ResolveError(friendly_error(exc)) from exc
    if not info:
        raise ResolveError("источник не найден")

    formats = [f for f in (info.get("formats") or []) if f.get("url")]
    if not formats and info.get("url") and _is_media_ext(info.get("ext") or extension_of(info["url"])):
        formats = [info]

    fmt = _pick_format(formats) if formats else None
    if fmt is not None:
        return ProbeResult(source=_source_from_format(info, fmt, url))
    return ProbeResult(needs_prepare=True, info=info)


def prepare_sync(url: str, room_id: str, info: dict[str, Any] | None = None) -> Source:
    if yt_dlp is None:
        raise ResolveError("yt-dlp не установлен")
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise ResolveError(
            "для этого источника нужен ffmpeg: установите его (winget install Gyan.FFmpeg) "
            "и перезапустите сервер"
        )

    out_dir = config.CACHE_DIR / room_id
    out_dir.mkdir(parents=True, exist_ok=True)
    height = config.YT_MAX_HEIGHT
    dl_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "max_filesize": config.YT_MAX_FILESIZE,
        "http_headers": {"User-Agent": config.USER_AGENT},
        "retries": 5,
        "ffmpeg_location": str(Path(ffmpeg).parent),
    }
    try:
        with yt_dlp.YoutubeDL(dl_options) as ydl:
            downloaded = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise ResolveError(friendly_error(exc).replace("разобрать", "скачать")) from exc

    path = _newest_media(out_dir)
    if path is None:
        raise ResolveError("ffmpeg не смог собрать файл")
    meta = downloaded or info or {}
    return Source(
        kind="local",
        origin="cache",
        input_url=url,
        ref=path.name,
        base="",
        title=meta.get("title") or "Видео",
        content_type="video/mp4",
        duration=meta.get("duration"),
        local=True,
    )


def _newest_media(out_dir: Path) -> Path | None:
    suffixes = {".mp4", ".mkv", ".webm", ".m4v"}
    candidates = [p for p in out_dir.glob("*") if p.suffix.lower() in suffixes]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


async def probe(url: str, title: str = "", bypass: bool = False) -> ProbeResult:
    try:
        return await asyncio.to_thread(probe_sync, url, title, bypass)
    except ResolveError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ResolveError(str(exc)) from exc


async def prepare(url: str, room_id: str, info: dict[str, Any] | None = None) -> Source:
    try:
        return await asyncio.to_thread(prepare_sync, url, room_id, info)
    except ResolveError:
        raise
    except Exception as exc:  # pragma: no cover
        raise ResolveError(str(exc)) from exc


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def ffmpeg_path() -> str | None:
    return find_ffmpeg()


def yt_dlp_available() -> bool:
    return yt_dlp is not None