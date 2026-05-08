from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting, TelegramUserApproval
from app.security import require_admin
from app.services.query_service import (
    list_distinct_types,
    read_distinct_values_filtered,
    read_fleet_history_filtered,
    read_job_orders,
    read_job_orders_filtered,
)
from app.services.telegram_service import set_setting
from app.services.telegram_service import (
    delete_webhook,
    get_setting,
    get_webhook_info,
    set_webhook,
    sync_updates_to_queue,
    test_telegram_token,
)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")


def _load_telegram_activity(db: Session, limit: int = 30) -> list[dict]:
    db.execute(
        text(
            """
            IF OBJECT_ID('dbo.telegram_message_logs', 'U') IS NULL
            CREATE TABLE dbo.telegram_message_logs (
                id INT IDENTITY(1,1) PRIMARY KEY,
                telegram_user_id NVARCHAR(100) NOT NULL,
                username NVARCHAR(150) NULL,
                full_name NVARCHAR(200) NULL,
                chat_id NVARCHAR(100) NULL,
                message_text NVARCHAR(MAX) NULL,
                source NVARCHAR(30) NOT NULL,
                created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            )
            """
        )
    )
    db.commit()
    return [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT TOP (:limit)
                    id, telegram_user_id, username, full_name, chat_id, message_text, source, created_at
                FROM dbo.telegram_message_logs
                ORDER BY id DESC
                """
            ),
            {"limit": limit},
        ).mappings()
    ]


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db), _user: str = Depends(require_admin)):
    pending = db.scalars(
        select(TelegramUserApproval)
        .where(TelegramUserApproval.is_approved == False)
        .order_by(TelegramUserApproval.created_at.desc())
    ).all()
    jobs = read_job_orders(db, limit=8)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "pending": pending, "jobs": jobs},
    )


@router.post("/approve/{approval_id}")
def approve_user(
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
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings, "message": ""},
    )


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
        "settings.html",
        {"request": request, "settings": settings, "message": "Settings saved successfully."},
    )


@router.get("/data/jobs", response_class=HTMLResponse)
def jobs_data_page(
    request: Request,
    search: str = "",
    customer: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    error = ""
    rows: list[dict] = []
    try:
        rows = read_job_orders_filtered(
            db,
            search=search,
            customer=customer,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    except Exception as exc:
        error = f"Jobs data view is unavailable: {exc}"

    return templates.TemplateResponse(
        "data_jobs.html",
        {
            "request": request,
            "rows": rows,
            "error": error,
            "filters": {
                "search": search,
                "customer": customer,
                "date_from": date_from,
                "date_to": date_to,
                "limit": limit,
            },
        },
    )


@router.get("/data/fleet-history", response_class=HTMLResponse)
def fleet_history_data_page(
    request: Request,
    plate_number: str = "",
    trxstatus: str = "",
    item_search: str = "",
    invoice_number: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    error = ""
    rows: list[dict] = []
    try:
        rows = read_fleet_history_filtered(
            db,
            plate_number=plate_number,
            trxstatus=trxstatus,
            item_search=item_search,
            invoice_number=invoice_number,
            limit=limit,
        )
    except Exception as exc:
        error = f"Fleet history view is unavailable: {exc}"

    return templates.TemplateResponse(
        "data_fleet_history.html",
        {
            "request": request,
            "rows": rows,
            "error": error,
            "filters": {
                "plate_number": plate_number,
                "trxstatus": trxstatus,
                "item_search": item_search,
                "invoice_number": invoice_number,
                "limit": limit,
            },
        },
    )


@router.get("/data/distinct-values", response_class=HTMLResponse)
def distinct_values_data_page(
    request: Request,
    value_type: str = "",
    value_search: str = "",
    trxstatus: str = "",
    limit: int = 200,
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    error = ""
    rows: list[dict] = []
    types: list[str] = []
    try:
        types = list_distinct_types(db)
        rows = read_distinct_values_filtered(
            db,
            value_type=value_type,
            value_search=value_search,
            trxstatus=trxstatus,
            limit=limit,
        )
    except Exception as exc:
        error = f"Distinct values view is unavailable: {exc}"

    return templates.TemplateResponse(
        "data_distinct_values.html",
        {
            "request": request,
            "rows": rows,
            "types": types,
            "error": error,
            "filters": {
                "value_type": value_type,
                "value_search": value_search,
                "trxstatus": trxstatus,
                "limit": limit,
            },
        },
    )


@router.get("/telegram", response_class=HTMLResponse)
async def telegram_setup_page(
    request: Request,
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    token = get_setting(db, "telegram_bot_token")
    mode = get_setting(db, "telegram_mode", "polling")
    enabled = get_setting(db, "telegram_enabled", "true").lower() == "true"
    info = {}
    error = ""
    activity: list[dict] = []
    if token:
        try:
            info = await get_webhook_info(token)
        except Exception as exc:
            error = f"Could not read webhook info: {exc}"
    try:
        activity = _load_telegram_activity(db, limit=30)
    except Exception:
        activity = []
    webhook_url = info.get("result", {}).get("url", "") if info else ""
    return templates.TemplateResponse(
        "telegram_setup.html",
        {
            "request": request,
            "token": token,
            "webhook_url": webhook_url,
            "message": "",
            "message_type": "info",
            "webhook_info": info,
            "last_update_id": get_setting(db, "telegram_last_update_id", "0"),
            "mode": mode,
            "enabled": enabled,
            "token_set": bool(token),
            "activity": activity,
            "error": error,
        },
    )


@router.get("/telegram/messages", response_class=HTMLResponse)
def telegram_messages_page(
    request: Request,
    search: str = "",
    user_id: str = "",
    source: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    error = ""
    rows: list[dict] = []
    try:
        db.execute(
            text(
                """
                IF OBJECT_ID('dbo.telegram_message_logs', 'U') IS NULL
                CREATE TABLE dbo.telegram_message_logs (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    telegram_user_id NVARCHAR(100) NOT NULL,
                    username NVARCHAR(150) NULL,
                    full_name NVARCHAR(200) NULL,
                    chat_id NVARCHAR(100) NULL,
                    message_text NVARCHAR(MAX) NULL,
                    source NVARCHAR(30) NOT NULL,
                    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                )
                """
            )
        )
        db.commit()

        clauses: list[str] = []
        params: dict[str, object] = {"limit": limit}
        if search:
            clauses.append("(message_text LIKE :search OR full_name LIKE :search OR username LIKE :search)")
            params["search"] = f"%{search}%"
        if user_id:
            clauses.append("telegram_user_id = :user_id")
            params["user_id"] = user_id
        if source:
            clauses.append("source = :source")
            params["source"] = source
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = [
            dict(r)
            for r in db.execute(
                text(
                    f"""
                    SELECT TOP (:limit)
                        id, telegram_user_id, username, full_name, chat_id, message_text, source, created_at
                    FROM dbo.telegram_message_logs
                    {where_clause}
                    ORDER BY id DESC
                    """
                ),
                params,
            ).mappings()
        ]

        # Backward-compatible fallback: if message logs are empty but approvals exist,
        # show synthetic rows so admins can still trace who contacted the bot.
        if not rows:
            clauses_approval: list[str] = []
            params_approval: dict[str, object] = {"limit": limit}
            if user_id:
                clauses_approval.append("telegram_user_id = :user_id")
                params_approval["user_id"] = user_id
            if search:
                clauses_approval.append("(full_name LIKE :search OR username LIKE :search)")
                params_approval["search"] = f"%{search}%"
            where_approval = f"WHERE {' AND '.join(clauses_approval)}" if clauses_approval else ""
            rows = [
                {
                    "created_at": r["created_at"],
                    "telegram_user_id": r["telegram_user_id"],
                    "full_name": r["full_name"],
                    "source": "approval-record",
                    "message_text": "(No message text captured. This user exists in approval queue.)",
                }
                for r in db.execute(
                    text(
                        f"""
                        SELECT TOP (:limit) telegram_user_id, username, full_name, created_at
                        FROM telegram_user_approvals
                        {where_approval}
                        ORDER BY created_at DESC
                        """
                    ),
                    params_approval,
                ).mappings()
            ]
    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(
        "telegram_messages.html",
        {
            "request": request,
            "rows": rows,
            "error": error,
            "filters": {"search": search, "user_id": user_id, "source": source, "limit": limit},
        },
    )


@router.post("/telegram", response_class=HTMLResponse)
async def telegram_setup_action(
    request: Request,
    action: str = Form(...),
    telegram_bot_token: str = Form(""),
    webhook_url: str = Form(""),
    telegram_mode: str = Form("polling"),
    telegram_enabled: bool = Form(False),
    db: Session = Depends(get_db),
    _user: str = Depends(require_admin),
):
    message = ""
    message_type = "info"
    info = {}
    error = ""
    activity: list[dict] = []
    token = telegram_bot_token.strip() or get_setting(db, "telegram_bot_token")
    if telegram_bot_token.strip():
        set_setting(db, "telegram_bot_token", telegram_bot_token.strip())
    set_setting(db, "telegram_mode", telegram_mode if telegram_mode in {"polling", "webhook"} else "polling")
    set_setting(db, "telegram_enabled", str(telegram_enabled))

    try:
        if action == "save_runtime":
            message = "Telegram runtime settings saved."
            message_type = "success"
            info = await get_webhook_info(token) if token else {}
        elif not token:
            raise ValueError("Telegram bot token is required.")
        elif action == "test_token":
            me = await test_telegram_token(token)
            bot_name = me.get("result", {}).get("username", "unknown")
            message = f"Token is valid. Connected bot: @{bot_name}"
            message_type = "success"
        elif action == "set_webhook":
            if not webhook_url.strip():
                raise ValueError("Webhook URL is required.")
            result = await set_webhook(token, webhook_url.strip())
            if result.get("ok"):
                message = "Webhook set successfully."
                message_type = "success"
            else:
                message = f"Webhook set response: {result}"
                message_type = "warning"
        elif action == "delete_webhook":
            result = await delete_webhook(token)
            if result.get("ok"):
                message = "Webhook removed successfully."
                message_type = "success"
            else:
                message = f"Webhook delete response: {result}"
                message_type = "warning"
        elif action == "sync_updates":
            sync_result = await sync_updates_to_queue(db, token)
            message = (
                f"Synced successfully. Processed {sync_result['processed_messages']} messages "
                f"from {sync_result['fetched_updates']} updates."
            )
            message_type = "success"
        else:
            raise ValueError("Unknown action.")

        info = await get_webhook_info(token)
    except Exception as exc:
        error = str(exc)
    try:
        activity = _load_telegram_activity(db, limit=30)
    except Exception:
        activity = []
    detected_webhook_url = info.get("result", {}).get("url", "") if info else ""
    mode = get_setting(db, "telegram_mode", "polling")
    enabled = get_setting(db, "telegram_enabled", "true").lower() == "true"

    return templates.TemplateResponse(
        "telegram_setup.html",
        {
            "request": request,
            "token": token,
            "webhook_url": webhook_url or detected_webhook_url,
            "message": message,
            "message_type": message_type,
            "webhook_info": info,
            "last_update_id": get_setting(db, "telegram_last_update_id", "0"),
            "mode": mode,
            "enabled": enabled,
            "token_set": bool(token),
            "activity": activity,
            "error": error,
        },
    )

