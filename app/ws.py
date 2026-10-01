"""WebSocket: чат, управление плеером, heartbeat синхронизации."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import config
from .rooms import RoomManager, send_json

router = APIRouter()

MAX_TEXT = config.MAX_MESSAGE_LENGTH


async def _reject(websocket: WebSocket, code: int, message: str) -> None:
    await websocket.accept()
    await send_json(websocket, {"type": "error", "message": message})
    await websocket.close(code=code)


@router.websocket("/ws/{room_id}")
async def room_socket(websocket: WebSocket, room_id: str) -> None:
    manager: RoomManager = websocket.app.state.manager
    room = manager.find(room_id)
    if room is None:
        await _reject(websocket, 4004, "комната не найдена")
        return

    uid = (websocket.query_params.get("uid") or "").strip()[:64]
    name = (websocket.query_params.get("name") or "").strip()[:32] or "Гость"
    if not uid:
        await _reject(websocket, 4003, "нужен идентификатор пользователя")
        return
    if len(room.users) >= config.MAX_PARTICIPANTS and uid not in room.users:
        await _reject(websocket, 4005, "в комнате уже максимум участников")
        return

    await websocket.accept()
    user, is_new = manager.add_user(room, uid, name, websocket)
    host_changed = manager.claim_host(room)

    messages = await manager.db.recent_messages(room.id, config.HISTORY_LIMIT)
    await send_json(
        websocket,
        {
            "type": "welcome",
            "you": {"id": user.id, "name": user.name, "host": room.host_id == user.id},
            "room": room.state_payload(),
            "peers": room.peers_payload()["users"],
            "history": messages,
        },
    )
    if host_changed:
        await manager.broadcast(room, {"type": "host", "host_id": room.host_id})
    await manager.broadcast(room, room.peers_payload())
    if is_new:
        await manager.announce(room, f"{user.name} присоединился")

    try:
        while True:
            data = await websocket.receive_json()
            await _dispatch(manager, room, user, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.remove_user(room, user)


async def _dispatch(manager: RoomManager, room, user, data: Any) -> None:
    if not isinstance(data, dict):
        return
    kind = data.get("type")

    if kind == "ping":
        await send_json(user.ws, {"type": "pong", "t": data.get("t"), "server_time": time.time()})
        return

    if kind == "chat":
        await manager.add_message(room, user, str(data.get("text", ""))[:MAX_TEXT])
        return

    if kind == "chat.seen":
        await manager.mark_seen(room, user, data.get("id"))
        return

    if kind == "typing":
        target = str(data.get("target", "chat"))
        await manager.broadcast(
            room, {"type": "typing", "user_id": user.id, "name": user.name, "target": target}
        )
        return

    if kind == "pb.control":
        try:
            position = data.get("position")
            rate = data.get("rate")
            await manager.apply_control(
                room,
                str(data.get("action", "")),
                float(position) if position is not None else None,
                float(rate) if rate is not None else None,
                user,
            )
        except (TypeError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "некорректная команда плеера"
            await send_json(user.ws, {"type": "error", "message": message})
        return

    if kind == "pb.report":
        await manager.report(room, user, data)
        return

    if kind == "pb.resync":
        await send_json(user.ws, room.state_payload("pb.sync"))
        return

    await send_json(user.ws, {"type": "error", "message": f"неизвестный тип: {kind}"})