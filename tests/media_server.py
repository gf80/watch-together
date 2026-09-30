"""Локальный HTTP-сервер с поддержкой Range — источник «фильма» для тестов.

Нужен, потому что в CI обычно нет интернета и ffmpeg: генерируем WAV
(стандартная библиотека), а <video> умеет играть аудиофайлы, поэтому вся
механика плеера проверяется по-настоящему.
"""
from __future__ import annotations

import math
import os
import struct
import threading
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ts": "video/mp2t",
    ".m3u8": "application/vnd.apple.mpegurl",
}


def make_hls(directory: str, port: int, segments: int = 3) -> str:
    """Готовит HLS-манифест с относительными и абсолютными сегментами."""
    hls_dir = os.path.join(directory, "hls")
    os.makedirs(hls_dir, exist_ok=True)
    for index in range(segments):
        with open(os.path.join(hls_dir, f"segment{index:03d}.ts"), "wb") as handle:
            handle.write(bytes([index]) * 4096)

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:4",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXTINF:4.000,",
        "segment000.ts",
        "#EXTINF:4.000,",
        f"http://127.0.0.1:{port}/hls/segment001.ts",
        "#EXTINF:4.000,",
        "segment002.ts",
        "#EXT-X-ENDLIST",
        "",
    ]
    with open(os.path.join(hls_dir, "index.m3u8"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return f"http://127.0.0.1:{port}/hls/index.m3u8"


def make_tone(path: str, seconds: int = 120, freq: int = 440) -> None:
    rate = 8000
    frames = bytearray()
    for index in range(rate * seconds):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * index / rate)))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


class RangeHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **MEDIA_TYPES}

    def log_message(self, *args) -> None:
        pass

    def _send_file(self) -> None:
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        media_type = MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        if not self.headers.get("Range"):
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as handle:
                self.wfile.write(handle.read())
            return
        spec = self.headers["Range"].replace("bytes=", "")
        start_raw, _, end_raw = spec.partition("-")
        start = int(start_raw or 0)
        end = int(end_raw) if end_raw else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            body = handle.read(end - start + 1)
        self.send_response(206)
        self.send_header("Content-Type", media_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _send_file
    do_HEAD = _send_file


def serve_media(directory: str, port: int) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler",
        (RangeHandler,),
        {"__init__": lambda self, *a, **k: SimpleHTTPRequestHandler.__init__(self, *a, directory=directory, **k)},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server