from __future__ import annotations

from datetime import datetime

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AppSetting, TelegramUserApproval
from app.services.query_service import read_fleet_history_by_plate, read_job_orders


def get_setting(db: Session, key: str, fallback: str = "") -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return row.value if row else fallback


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def ensure_telegram_user(db: Session, tg_user_id: str, username: str, full_name: str) -> TelegramUserApproval:
    user = db.scalar(
        select(TelegramUserApproval).where(TelegramUserApproval.telegram_user_id == tg_user_id)
    )
    if user:
        if username:
            user.username = username
        if full_name:
            user.full_name = full_name
    else:
        user = TelegramUserApproval(
            telegram_user_id=tg_user_id,
            username=username,
            full_name=full_name,
            is_approved=False,
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


async def send_telegram_message(db: Session, chat_id: str, text: str) -> None:
    token = get_setting(db, "telegram_bot_token")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json={"chat_id": chat_id, "text": text})
    log_outbound_message(db, chat_id=chat_id, text_value=text)


async def telegram_api_call(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload or {})
    response.raise_for_status()
    return response.json()


async def test_telegram_token(token: str) -> dict:
    return await telegram_api_call(token, "getMe")


async def get_webhook_info(token: str) -> dict:
    return await telegram_api_call(token, "getWebhookInfo")


async def set_webhook(token: str, webhook_url: str) -> dict:
    return await telegram_api_call(token, "setWebhook", {"url": webhook_url})


async def delete_webhook(token: str) -> dict:
    return await telegram_api_call(token, "deleteWebhook")


async def sync_updates_to_queue(db: Session, token: str, limit: int = 100) -> dict:
    last_update_id_raw = get_setting(db, "telegram_last_update_id", "0")
    try:
        last_update_id = int(last_update_id_raw)
    except ValueError:
        last_update_id = 0

    payload = {"limit": limit, "timeout": 0, "allowed_updates": ["message"]}
    if last_update_id > 0:
        payload["offset"] = last_update_id + 1

    result = await telegram_api_call(token, "getUpdates", payload)
    updates = result.get("result", [])
    processed = 0
    max_update_id = last_update_id

    for upd in updates:
        update_id = int(upd.get("update_id", 0))
        if update_id > max_update_id:
            max_update_id = update_id
        message = upd.get("message")
        if message:
            await handle_message(db, message, source="polling")
            processed += 1

    if max_update_id > last_update_id:
        set_setting(db, "telegram_last_update_id", str(max_update_id))

    return {"processed_messages": processed, "fetched_updates": len(updates), "last_update_id": max_update_id}


async def auto_poll_once(db: Session) -> dict:
    enabled = get_setting(db, "telegram_enabled", "true").lower() == "true"
    mode = get_setting(db, "telegram_mode", "polling").lower()
    token = get_setting(db, "telegram_bot_token", "")
    if not enabled:
        return {"status": "disabled"}
    if mode != "polling":
        return {"status": "mode_not_polling"}
    if not token:
        return {"status": "no_token"}
    result = await sync_updates_to_queue(db, token, limit=50)
    result["status"] = "ok"
    return result


def _ensure_message_log_table(db: Session) -> None:
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


def log_incoming_message(
    db: Session,
    tg_id: str,
    username: str,
    full_name: str,
    chat_id: str,
    text_value: str,
    source: str = "webhook",
) -> None:
    _ensure_message_log_table(db)
    db.execute(
        text(
            """
            INSERT INTO dbo.telegram_message_logs
            (telegram_user_id, username, full_name, chat_id, message_text, source)
            VALUES (:tg_id, :username, :full_name, :chat_id, :message_text, :source)
            """
        ),
        {
            "tg_id": tg_id,
            "username": username,
            "full_name": full_name,
            "chat_id": chat_id,
            "message_text": text_value[:4000],
            "source": source,
        },
    )
    db.commit()


def log_outbound_message(db: Session, chat_id: str, text_value: str) -> None:
    _ensure_message_log_table(db)
    db.execute(
        text(
            """
            INSERT INTO dbo.telegram_message_logs
            (telegram_user_id, username, full_name, chat_id, message_text, source)
            VALUES (:tg_id, :username, :full_name, :chat_id, :message_text, :source)
            """
        ),
        {
            "tg_id": chat_id,
            "username": "",
            "full_name": "Bot Reply",
            "chat_id": chat_id,
            "message_text": text_value[:4000],
            "source": "bot_reply",
        },
    )
    db.commit()


async def handle_message(db: Session, message: dict, source: str = "webhook") -> None:
    from_user = message.get("from", {})
    tg_id = str(from_user.get("id", ""))
    username = from_user.get("username", "")
    full_name = f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip()
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not (tg_id and chat_id):
        return
    log_incoming_message(db, tg_id, username, full_name, chat_id, text, source=source)

    user = ensure_telegram_user(db, tg_id, username, full_name)
    if not user.is_approved:
        await send_telegram_message(
            db,
            chat_id,
            "Your account is pending admin approval. Please wait.",
        )
        return

    if text.startswith("/jobs"):
        rows = read_job_orders(db, limit=5)
        msg = "\n".join([f"#{r['JobID']} | {r['JobCustName']} | {r['JobDate']}" for r in rows]) or "No jobs found."
        await send_telegram_message(db, chat_id, msg)
        return

    if text.startswith("/plate "):
        plate = text.replace("/plate ", "", 1).strip()
        rows = read_fleet_history_by_plate(db, plate, limit=5)
        if not rows:
            await send_telegram_message(db, chat_id, "No history found for this plate.")
            return
        msg = "\n".join(
            [f"{r['Purchase Date']} | {r['Item Description']} | {r['Amount USD']} USD" for r in rows]
        )
        await send_telegram_message(db, chat_id, msg)
        return

    ai_url = get_setting(db, "ai_api_url")
    ai_key = get_setting(db, "ai_api_key")
    ai_model = get_setting(db, "ai_model")
    if ai_url and ai_key and ai_model:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                ai_url,
                json={"model": ai_model, "prompt": text},
                headers={"Authorization": f"Bearer {ai_key}"},
            )
            content = response.text[:3500]
            await send_telegram_message(db, chat_id, content or "AI returned empty response.")
            return

    await send_telegram_message(
        db,
        chat_id,
        "Supported commands: /jobs, /plate <PLATE_NUMBER>. AI fallback is not configured.",
    )

