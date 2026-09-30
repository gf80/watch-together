"""Определение типа видео по ссылке: прямой файл или HLS."""
from __future__ import annotations

from urllib.parse import urlparse

HLS_TYPE = "application/vnd.apple.mpegurl"
HLS_SUFFIXES = (".m3u8", ".m3u")

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".m4s": "video/iso.segment",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".ogv": "video/ogg",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
    ".ts": "video/mp2t",
    ".m3u8": HLS_TYPE,
    ".m3u": HLS_TYPE,
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".vtt": "text/vtt",
    ".json": "application/json",
    ".key": "application/octet-stream",
}

PROGRESSIVE_EXTENSIONS = (
    ".mp4",
    ".webm",
    ".m4v",
    ".mov",
    ".mkv",
    ".ogv",
    ".mp3",
    ".m4a",
    ".aac",
    ".opus",
    ".ogg",
    ".wav",
    ".flac",
)


def url_path(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except ValueError:
        return ""


def looks_like_hls(url: str) -> bool:
    return url_path(url).endswith(HLS_SUFFIXES)


def looks_like_media_file(url: str) -> bool:
    path = url_path(url)
    return path.endswith(PROGRESSIVE_EXTENSIONS)


def media_type_for(ref: str) -> str:
    path = url_path(ref)
    for suffix, media_type in MEDIA_TYPES.items():
        if path.endswith(suffix):
            return media_type
    return "application/octet-stream"


def extension_of(url: str) -> str:
    path = url_path(url)
    if "." not in path.rsplit("/", 1)[-1]:
        return ""
    return "." + path.rsplit("/", 1)[-1].rsplit(".", 1)[-1]