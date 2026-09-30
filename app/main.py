"""Watch Together — совместный просмотр с общей паузой и чатом."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, proxy
from .api import router as api_router
from .db import Database
from .rooms import RoomManager
from .ws import router as ws_router


async def _sweeper(manager: RoomManager) -> None:
    while True:
        with contextlib.suppress(Exception):
            await manager.sweep()
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db = Database()
    await db.connect()
    manager = RoomManager(db)
    await manager.load()
    app.state.db = db
    app.state.manager = manager
    sweeper = asyncio.create_task(_sweeper(manager))
    try:
        yield
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await proxy.close_client()
        await db.close()


app = FastAPI(title="Watch Together", lifespan=lifespan, docs_url="/api/docs")
app.include_router(api_router)
app.include_router(ws_router)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/r/{room_id}")
async def room_page(room_id: str) -> FileResponse:
    return FileResponse(config.STATIC_DIR / "room.html")


@app.get("/room/{room_id}")
async def room_page_alias(room_id: str) -> RedirectResponse:
    return RedirectResponse(f"/r/{room_id}", status_code=308)