from __future__ import annotations

from datetime import datetime

import httpx
from sqlalchemy import select
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


async def handle_message(db: Session, message: dict) -> None:
    from_user = message.get("from", {})
    tg_id = str(from_user.get("id", ""))
    username = from_user.get("username", "")
    full_name = f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip()
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not (tg_id and chat_id):
        return

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

