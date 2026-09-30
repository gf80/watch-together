"""Прокси видеопотока.

Зачем он нужен при ссылках на внешний источник:
  * CDN обычно не отдаёт CORS-заголовки, а hls.js/XHR их требуют;
  * многие хотят Referer исходного сайта, иначе 403;
  * Range-запросы нужны для перемотки, часть CDN их не поддерживает.

Дополнительно: HLS-манифесты переписываются, чтобы все сегменты шли
через прокси (same-origin), и их хосты попадают в allowlist комнаты.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import config
from .media import HLS_TYPE, media_type_for
from .net import BlockedUrl, check_public_url
from .rooms import Room, host_of, stream_ref_for

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
URL_TOKEN_RE = re.compile(r"""https?://[^\s"'<>,;|]+""")

FORWARDED_RESPONSE_HEADERS = (
    "content-range",
    "content-length",
    "content-type",
    "accept-ranges",
    "etag",
    "last-modified",
)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(config.PROXY_TIMEOUT, connect=10.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def allowed_host(room: Room, host: str) -> bool:
    source = room.source
    if source is None:
        return False
    return bool(host) and host in set(source.allowed_hosts)


def proxy_url(room_id: str, ref: str) -> str:
    return f"/api/stream/{room_id}/{quote(ref, safe='')}"


def rewrite_manifest(room: Room, text: bytes) -> bytes:
    source = room.source
    hosts = set(source.allowed_hosts) if source else set()

    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        host = host_of(url)
        if host:
            hosts.add(host)
        return proxy_url(room.id, url)

    decoded = text.decode("utf-8", "replace")
    rewritten = URL_TOKEN_RE.sub(_replace, decoded)
    if source is not None:
        source.allowed_hosts = sorted(hosts)
    return rewritten.encode("utf-8")


def _resolve_local(room: Room, ref: str) -> Path | None:
    source = room.source
    if source is None or not source.local:
        return None
    root = (config.CACHE_DIR / room.id).resolve()
    target = (root / ref).resolve()
    if root != target and root not in target.parents:
        return None
    return target if target.is_file() else None


def _file_response(path: Path, request: Request, media_type: str) -> Response:
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    range_header = request.headers.get("range")
    if range_header:
        match = RANGE_RE.fullmatch(range_header.strip())
        if match:
            raw_start, raw_end = match.group(1), match.group(2)
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else size - 1
            elif raw_end:
                start = max(0, size - int(raw_end))
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    length = max(0, end - start + 1)
    if length > config.PROXY_MAX_BYTES:
        end = start + config.PROXY_MAX_BYTES - 1
        length = config.PROXY_MAX_BYTES

    def body():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(config.CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "accept-ranges": "bytes",
        "content-length": str(length),
        "access-control-allow-origin": "*",
        "cache-control": "no-store",
    }
    if status == 206:
        headers["content-range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=headers)


async def proxy_stream(room: Room, ref: str, request: Request) -> Response:
    source = room.source
    if source is None:
        return JSONResponse({"error": "в комнате нет источника"}, status_code=404)

    local_path = _resolve_local(room, ref)
    if local_path is not None:
        return _file_response(local_path, request, source.content_type)

    upstream = stream_ref_for(room, ref)
    try:
        await check_public_url(upstream)
    except BlockedUrl as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)

    host = host_of(upstream)
    if not allowed_host(room, host):
        return JSONResponse({"error": f"хост {host or '?'} не разрешён для этой комнаты"}, status_code=403)

    headers: dict[str, str] = {
        "User-Agent": source.headers.get("User-Agent") or config.USER_AGENT,
        "Accept": "*/*",
    }
    for key in ("Referer", "Origin", "Cookie", "Accept-Language"):
        value = source.headers.get(key)
        if value:
            headers[key] = value
    for key in ("range", "if-range", "if-none-match", "if-modified-since"):
        value = request.headers.get(key)
        if value:
            headers[key] = value

    client = get_client()
    try:
        upstream_response = await client.send(
            client.build_request("GET", upstream, headers=headers), stream=True
        )
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"не удалось получить поток: {exc}"}, status_code=502)

    content_type = (
        upstream_response.headers.get("content-type")
        or media_type_for(upstream)
        or "application/octet-stream"
    )
    if content_type.split(";")[0].strip().lower() in (
        HLS_TYPE,
        "application/x-mpegurl",
        "audio/mpegurl",
    ):
        raw = await upstream_response.aread()
        await upstream_response.aclose()
        return Response(
            content=rewrite_manifest(room, raw),
            media_type=HLS_TYPE,
            headers={"access-control-allow-origin": "*", "cache-control": "no-store"},
        )

    async def body():
        try:
            total = 0
            async for chunk in upstream_response.aiter_raw(config.CHUNK_SIZE):
                total += len(chunk)
                if total > config.PROXY_MAX_BYTES:
                    break
                yield chunk
        finally:
            await upstream_response.aclose()

    response_headers: dict[str, str] = {}
    for key in FORWARDED_RESPONSE_HEADERS:
        value = upstream_response.headers.get(key)
        if value:
            response_headers[key.lower()] = value
    response_headers["access-control-allow-origin"] = "*"
    response_headers["cache-control"] = "no-store"
    response_headers.setdefault("accept-ranges", "bytes")

    return StreamingResponse(
        body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=None if "content-type" in response_headers else content_type,
    )