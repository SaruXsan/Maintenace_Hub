from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting, TelegramUserApproval
from app.security import require_admin
from app.services.query_service import read_job_orders
from app.services.telegram_service import set_setting

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db), _user: str = Depends(require_admin)):
    pending = db.scalars(
        select(TelegramUserApproval).order_by(TelegramUserApproval.created_at.desc())
    ).all()
    jobs = read_job_orders(db, limit=8)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"pending": pending, "jobs": jobs},
    )


@router.post("/approve/{approval_id}")
def approve_user(
    request: Request,
    approval_id: int,
    approved: bool = Form(...),
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    row = db.scalar(select(TelegramUserApproval).where(TelegramUserApproval.id == approval_id))
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    row.is_approved = approved
    row.approved_at = datetime.utcnow() if approved else None
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), _user: str = Depends(require_admin)):
    rows = db.scalars(select(AppSetting)).all()
    settings = {r.key: r.value for r in rows}
    return templates.TemplateResponse(request, "settings.html", {"settings": settings, "message": ""})


@router.post("/settings", response_class=HTMLResponse)
def save_settings(
    request: Request,
    telegram_bot_token: str = Form(""),
    ad_server: str = Form(""),
    ad_domain: str = Form(""),
    ai_api_url: str = Form(""),
    ai_api_key: str = Form(""),
    ai_model: str = Form(""),
    dev_mode: bool = Form(False),
    dev_admin_user: str = Form("admin"),
    dev_admin_password: str = Form("admin123"),
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    set_setting(db, "telegram_bot_token", telegram_bot_token)
    set_setting(db, "ad_server", ad_server)
    set_setting(db, "ad_domain", ad_domain)
    set_setting(db, "ai_api_url", ai_api_url)
    set_setting(db, "ai_api_key", ai_api_key)
    set_setting(db, "ai_model", ai_model)
    set_setting(db, "dev_mode", str(dev_mode))
    set_setting(db, "dev_admin_user", dev_admin_user)
    set_setting(db, "dev_admin_password", dev_admin_password)
    rows = db.scalars(select(AppSetting)).all()
    settings = {r.key: r.value for r in rows}
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "message": "Settings saved successfully."},
    )

