from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import load_bootstrap
from app.routers import admin, api, auth, setup, telegram

app = FastAPI(title="Maintenance Hub Portal", version="1.0.0")
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

