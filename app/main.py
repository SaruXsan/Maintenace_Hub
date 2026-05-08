from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import load_bootstrap
from app.database import get_engine
from app.routers import admin, api, auth, setup, telegram
from app.services.telegram_service import auto_poll_once


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_event = asyncio.Event()

    async def poll_loop():
        while not stop_event.is_set():
            try:
                engine = get_engine()
                if engine is not None:
                    with Session(engine) as db:
                        await auto_poll_once(db)
            except Exception:
                # Keep service alive even if one cycle fails.
                pass
            await asyncio.sleep(10)

    task = asyncio.create_task(poll_loop())
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except Exception:
            pass


app = FastAPI(title="Maintenance Hub Portal", version="1.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key="change-me-session-key")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(api.router)
app.include_router(telegram.router)


@app.get("/")
def root():
    cfg = load_bootstrap()
    return RedirectResponse("/login" if cfg.has_db else "/setup", status_code=303)


@app.exception_handler(RuntimeError)
def runtime_handler(_: Request, exc: RuntimeError):
    if "Database is not configured" in str(exc):
        return RedirectResponse("/setup", status_code=303)
    return RedirectResponse("/setup", status_code=303)

