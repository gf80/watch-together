"""REST API: комнаты, история чата, управление, прокси потока, сессия."""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from . import config, proxy, resolver
from .net import BlockedUrl, check_public_url
from .rooms import Room, RoomManager, host_of

router = APIRouter()

COOKIE_UID = "wt_uid"


def manager_of(request: Request) -> RoomManager:
    return request.app.state.manager


def client_id(request: Request) -> str:
    header = request.headers.get("x-user-id")
    if header:
        return header.strip()[:64]
    cookie = request.cookies.get(COOKIE_UID)
    if cookie:
        try:
            return unquote(cookie).strip()[:64]
        except ValueError:
            return ""
    return ""


def require_room(request: Request, room_id: str) -> Room | None:
    return manager_of(request).find(room_id)


def is_member(room: Room, uid: str) -> bool:
    return bool(uid) and (uid in room.users or uid == room.host_id)


def host_guard(room: Room, uid: str) -> Response | None:
    if uid and uid == room.host_id:
        return None
    return Response(
        content='{"error":"это действие доступно хосту комнаты"}',
        status_code=403,
        media_type="application/json",
    )


def json_error(message: str, status: int = 400) -> Response:
    return json_body({"error": message}, status)


def json_body(payload: Any, status: int = 200) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        status_code=status,
        media_type="application/json",
    )


class SessionBody(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=32)


class CreateRoomBody(BaseModel):
    title: str = Field(default="", max_length=80)


class SourceBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=120)
    bypass: bool = False


class ControlBody(BaseModel):
    action: str
    position: float | None = None
    rate: float | None = None


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=config.MAX_MESSAGE_LENGTH)


@router.post("/api/session")
async def create_session(body: SessionBody) -> Response:
    response = json_body({"ok": True})
    response.set_cookie(
        COOKIE_UID,
        quote(body.uid, safe=""),
        max_age=60 * 60 * 24 * 365,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/api/rooms")
async def create_room(body: CreateRoomBody, request: Request) -> Response:
    manager = manager_of(request)
    uid = client_id(request) or "anon"
    if len(manager.rooms) >= config.MAX_ROOMS:
        return json_error("слишком много комнат, удалите неактивные", 507)
    room = await manager.create(body.title, uid)
    return json_body(
        {
            "id": room.id,
            "title": room.title,
            "host_id": room.host_id,
            "url": f"/r/{room.id}",
        },
        status=201,
    )


@router.get("/api/rooms")
async def list_rooms(request: Request) -> Response:
    manager = manager_of(request)
    rows = await manager.db.room_summaries()
    items = []
    for row in rows:
        room = manager.find(row["id"])
        if room is None:
            continue
        items.append(
            {
                "id": room.id,
                "title": room.title or "Без названия",
                "source_title": row.get("source_title", ""),
                "status": room.status,
                "source_status": room.source_status,
                "participants": len(room.users),
                "host_id": room.host_id,
                "is_host": room.host_id == client_id(request),
                "created_at": room.created_at,
            }
        )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return json_body({"rooms": items})


@router.get("/api/rooms/{room_id}")
async def get_room(room_id: str, request: Request) -> Response:
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    return json_body({"room": room.state_payload(), "peers": room.peers_payload()["users"]})


@router.delete("/api/rooms/{room_id}")
async def delete_room(room_id: str, request: Request) -> Response:
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    denied = host_guard(room, client_id(request))
    if denied is not None:
        return denied
    await manager_of(request).delete(room.id)
    return json_body({"ok": True})


@router.get("/api/rooms/{room_id}/messages")
async def history(room_id: str, request: Request, limit: int = config.HISTORY_LIMIT) -> Response:
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    rows = await manager_of(request).db.recent_messages(
        room.id, max(1, min(limit, config.HISTORY_LIMIT))
    )
    return json_body({"messages": rows})


@router.post("/api/rooms/{room_id}/messages")
async def post_message(room_id: str, body: MessageBody, request: Request) -> Response:
    manager = manager_of(request)
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    user = room.users.get(client_id(request))
    if user is None:
        return json_error("подключитесь к комнате через WebSocket", 409)
    message = await manager.add_message(room, user, body.text)
    return json_body(message or {})


@router.post("/api/rooms/{room_id}/source")
async def set_source(room_id: str, body: SourceBody, request: Request) -> Response:
    manager = manager_of(request)
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    denied = host_guard(room, client_id(request))
    if denied is not None:
        return denied

    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        return json_error("нужна ссылка, начинающаяся с http:// или https://")

    try:
        probe = await resolver.probe(url, body.title.strip(), body.bypass)
    except resolver.ResolveError as exc:
        return json_error(str(exc))

    if probe.source is not None:
        if not probe.source.bypass:
            host = host_of(probe.source.ref)
            try:
                await check_public_url(probe.source.ref)
            except BlockedUrl as exc:
                return json_error(str(exc))
            if host:
                probe.source.allowed_hosts = sorted(set(probe.source.allowed_hosts) | {host})
        await manager.set_source(room, probe.source)
        return json_body(
            {
                "ok": True,
                "status": room.source_status,
                "source": room.source_payload(),
                "note": probe.note,
            }
        )

    await manager.set_source_preparing(room)
    manager.spawn(_prepare_source(manager, room, url, probe.info))
    return json_body({"ok": True, "status": "preparing"})


async def _prepare_source(manager: RoomManager, room: Room, url: str, info: dict[str, Any]) -> None:
    try:
        source = await resolver.prepare(url, room.id, info)
    except Exception as exc:
        await manager.set_source_error(room, str(exc))
        return
    if room.id not in manager.rooms:
        return
    await manager.set_source(room, source)


@router.post("/api/rooms/{room_id}/control")
async def control(room_id: str, body: ControlBody, request: Request) -> Response:
    manager = manager_of(request)
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    actor = room.users.get(client_id(request))
    try:
        payload = await manager.apply_control(room, body.action, body.position, body.rate, actor)
    except ValueError as exc:
        return json_error(str(exc))
    return json_body(payload)


@router.get("/api/capabilities")
async def capabilities(request: Request) -> Response:
    return json_body(
        {
            "yt_dlp": resolver.yt_dlp_available(),
            "ffmpeg": resolver.ffmpeg_available(),
            "ffmpeg_path": resolver.ffmpeg_path(),
            "max_height": config.YT_MAX_HEIGHT,
            "allow_private": config.ALLOW_PRIVATE_PROXY,
        }
    )


@router.get("/api/stream/{room_id}/{ref:path}")
async def stream(room_id: str, ref: str, request: Request) -> Response:
    room = require_room(request, room_id)
    if room is None:
        return json_error("комната не найдена", 404)
    uid = client_id(request) or request.query_params.get("uid", "")
    if not is_member(room, uid):
        return json_error("нужно войти в комнату", 403)
    return await proxy.proxy_stream(room, unquote(ref), request)


@router.get("/api/health")
async def health(request: Request) -> Response:
    manager = manager_of(request)
    return json_body({"ok": True, "rooms": len(manager.rooms), "time": time.time()})