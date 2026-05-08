from __future__ import annotations

from fastapi import HTTPException, Request
from ldap3 import ALL, Connection, Server
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_bootstrap
from app.models import AppSetting


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_admin(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def login_user(request: Request, username: str) -> None:
    request.session["user"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def _get_setting(db: Session, key: str, fallback: str = "") -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return row.value if row else fallback


def authenticate(db: Session, username: str, password: str) -> bool:
    cfg = load_bootstrap()
    dev_mode = _get_setting(db, "dev_mode", str(cfg.dev_mode)).lower() == "true"
    dev_admin_user = _get_setting(db, "dev_admin_user", cfg.dev_admin_user)
    dev_admin_password = _get_setting(db, "dev_admin_password", cfg.dev_admin_password)

    if dev_mode and username == dev_admin_user and password == dev_admin_password:
        return True

    ad_server = _get_setting(db, "ad_server", cfg.ad_server)
    ad_domain = _get_setting(db, "ad_domain", cfg.ad_domain)
    if ad_server and ad_domain:
        server = Server(ad_server, get_info=ALL)
        user_dn = f"{ad_domain}\\{username}"
        try:
            conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            conn.unbind()
            return True
        except Exception:
            pass

    return False

