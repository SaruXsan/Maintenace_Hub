from __future__ import annotations

import json
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AppSetting, TelegramUserApproval
from app.services.query_service import (
    count_fleet_history_filtered,
    read_fleet_history_by_plate,
    read_fleet_history_filtered,
    read_fleet_history_filtered_page,
    read_job_orders,
)


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


async def send_telegram_message(
    db: Session, chat_id: str, message_text: str, parse_mode: str | None = None
) -> None:
    token = get_setting(db, "telegram_bot_token")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    plain: dict[str, object] = {"chat_id": chat_id, "text": message_text}

    async def _post(client: httpx.AsyncClient, mode: str | None) -> httpx.Response:
        body: dict[str, object] = dict(plain)
        if mode:
            body["parse_mode"] = mode
        return await client.post(url, json=body)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await _post(client, parse_mode)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            if parse_mode:
                response = await _post(client, None)
                response.raise_for_status()
            else:
                raise

        data = response.json()
        if not data.get("ok") and parse_mode:
            response = await _post(client, None)
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            short = (message_text or "")[:4090]
            if short != message_text or len(message_text or "") > 4096:
                response = await client.post(url, json={"chat_id": chat_id, "text": short})
                response.raise_for_status()
                data = response.json()
        if not data.get("ok"):
            return
    log_outbound_message(db, chat_id=chat_id, text_value=message_text)


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


def _ensure_ai_log_table(db: Session) -> None:
    db.execute(
        text(
            """
            IF OBJECT_ID('dbo.telegram_ai_logs', 'U') IS NULL
            CREATE TABLE dbo.telegram_ai_logs (
                id INT IDENTITY(1,1) PRIMARY KEY,
                telegram_user_id NVARCHAR(100) NULL,
                chat_id NVARCHAR(100) NULL,
                input_text NVARCHAR(MAX) NULL,
                ai_url NVARCHAR(1000) NULL,
                ai_model NVARCHAR(200) NULL,
                status NVARCHAR(50) NOT NULL,
                error_message NVARCHAR(MAX) NULL,
                response_preview NVARCHAR(MAX) NULL,
                created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            )
            """
        )
    )
    db.commit()


def log_ai_attempt(
    db: Session,
    tg_id: str,
    chat_id: str,
    input_text: str,
    ai_url: str,
    ai_model: str,
    status: str,
    error_message: str = "",
    response_preview: str = "",
) -> None:
    _ensure_ai_log_table(db)
    db.execute(
        text(
            """
            INSERT INTO dbo.telegram_ai_logs
            (telegram_user_id, chat_id, input_text, ai_url, ai_model, status, error_message, response_preview)
            VALUES (:tg_id, :chat_id, :input_text, :ai_url, :ai_model, :status, :error_message, :response_preview)
            """
        ),
        {
            "tg_id": tg_id,
            "chat_id": chat_id,
            "input_text": input_text[:4000],
            "ai_url": ai_url[:1000],
            "ai_model": ai_model[:200],
            "status": status[:50],
            "error_message": error_message[:4000],
            "response_preview": response_preview[:4000],
        },
    )
    db.commit()


def _is_openai_chat_endpoint(ai_url: str) -> bool:
    normalized = ai_url.strip().lower()
    return normalized.endswith("/v1/chat/completions") or "/chat/completions" in normalized


def _extract_ai_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text

    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
                text_value = first.get("text")
                if isinstance(text_value, str):
                    return text_value
        direct_text = payload.get("text")
        if isinstance(direct_text, str):
            return direct_text
    return response.text


def _extract_plate_candidate(text_value: str) -> str:
    # Typical plate inputs in this project are numeric (example: 104682).
    match = re.search(r"\b\d{4,10}\b", text_value)
    return match.group(0) if match else ""


def _looks_like_plate_intent(text_value: str) -> bool:
    normalized = text_value.lower()
    keywords = ("plate", "plate number", "platenumber", "vehicle history", "fleet history")
    return any(word in normalized for word in keywords)


async def _send_main_menu(db: Session, chat_id: str) -> None:
    await send_telegram_message(
        db,
        chat_id,
        (
            "*Welcome to Maintenance Fleet System*\n"
            "Press `1` to search for data."
        ),
        parse_mode="Markdown",
    )


def _flow_state_key(tg_id: str) -> str:
    return f"telegram_flow_state:{tg_id}"


def _last_message_key(chat_id: str) -> str:
    return f"telegram_last_message_id:{chat_id}"


def _get_flow_state(db: Session, tg_id: str) -> dict:
    raw = get_setting(db, _flow_state_key(tg_id), "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_flow_state(db: Session, tg_id: str, state: dict) -> None:
    set_setting(db, _flow_state_key(tg_id), json.dumps(state))


def _clear_flow_state(db: Session, tg_id: str) -> None:
    set_setting(db, _flow_state_key(tg_id), "")


def _wants_yes(text_value: str) -> bool:
    normalized = text_value.strip().lower()
    return normalized in {"yes", "y", "1", "true", "ok"}


def _wants_no(text_value: str) -> bool:
    normalized = text_value.strip().lower()
    return normalized in {"no", "n", "2", "false", "cancel", "stop"}


def _fleet_row_supplier(row: dict) -> str:
    for key in ("supplier_name", "Source", "source", "Supplier Name", "Supplier", "supplier"):
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return "-"


def _fleet_row_quantity(row: dict) -> str:
    for key in ("Qty", "Quantity", "qty", "quantity"):
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return "-"


def _send_fleet_report_email(
    db: Session,
    recipient_email: str,
    plate_number: str,
    newest_first: bool,
    description_filter: str,
    rows: list[dict],
) -> tuple[bool, str]:
    mail_host = get_setting(db, "mail_smtp_host", "smtp.office365.com")
    mail_port = int(get_setting(db, "mail_smtp_port", "587") or "587")
    mail_username = get_setting(db, "mail_username")
    mail_password = get_setting(db, "mail_password")
    mail_from_email = get_setting(db, "mail_from_email")
    mail_from_name = get_setting(db, "mail_from_name", "Maintenance Hub")

    if not (mail_username and mail_password and mail_from_email):
        return False, "Mail sender is not configured in Settings."

    direction = "New to old" if newest_first else "Old to new"
    description_label = description_filter or "N/A"
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    subject = f"Fleet History Report - Plate {plate_number}"

    text_lines = [
        "Fleet History Report",
        f"Plate Number: {plate_number}",
        f"Order: {direction}",
        f"Description Filter: {description_label}",
        f"Generated At: {generated_at}",
        "",
    ]
    for idx, row in enumerate(rows, start=1):
        qty = _fleet_row_quantity(row)
        sup = _fleet_row_supplier(row)
        text_lines.extend(
            [
                f"{idx}. Purchase Date: {row.get('Purchase Date', '-')}",
                f"   Invoice Number: {row.get('Invoice Number', '-')}",
                f"   Item Description: {row.get('Item Description', '-')}",
                f"   Quantity: {qty}",
                f"   Supplier: {sup}",
                f"   Amount USD: {row.get('Amount USD', '-')}",
                f"   Amount LBP: {row.get('Amount LBP', '-')}",
                "",
            ]
        )
    text_body = "\n".join(text_lines)

    row_html = "".join(
        [
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{row.get('Purchase Date', '-')}</td>"
            f"<td>{row.get('Invoice Number', '-')}</td>"
            f"<td>{row.get('Plate Number', '-')}</td>"
            f"<td>{row.get('Item Description', '-')}</td>"
            f"<td>{_fleet_row_quantity(row)}</td>"
            f"<td>{_fleet_row_supplier(row)}</td>"
            f"<td>{row.get('Amount USD', '-')}</td>"
            f"<td>{row.get('Amount LBP', '-')}</td>"
            "</tr>"
            for idx, row in enumerate(rows, start=1)
        ]
    )
    html_body = (
        "<html><body>"
        "<h2>Fleet History Report</h2>"
        f"<p><strong>Plate Number:</strong> {plate_number}<br>"
        f"<strong>Order:</strong> {direction}<br>"
        f"<strong>Description Filter:</strong> {description_label}<br>"
        f"<strong>Generated At:</strong> {generated_at}</p>"
        "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>"
        "<thead><tr>"
        "<th>#</th><th>Purchase Date</th><th>Invoice Number</th><th>Plate Number</th>"
        "<th>Item Description</th><th>Quantity</th><th>Supplier</th><th>Amount USD</th><th>Amount LBP</th>"
        "</tr></thead>"
        f"<tbody>{row_html}</tbody>"
        "</table>"
        "</body></html>"
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{mail_from_name} <{mail_from_email}>"
    message["To"] = recipient_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(mail_host, mail_port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(mail_username, mail_password)
        smtp.send_message(message)
    return True, f"Report sent successfully to {recipient_email}."


def _format_fleet_rows_markdown(rows: list[dict], start_index: int = 1) -> str:
    """Plain-text fleet rows (no Telegram Markdown) so Arabic/special chars never break sendMessage."""
    lines = ["Fleet History Results", ""]
    for idx, row in enumerate(rows, start=start_index):
        qty = _fleet_row_quantity(row)
        sup = _fleet_row_supplier(row)
        lines.extend(
            [
                f"{idx}) Purchase Date: {row.get('Purchase Date', '-')}",
                f"   Invoice: {row.get('Invoice Number', '-')} | Plate: {row.get('Plate Number', '-')}",
                f"   Description: {row.get('Item Description', '-')}",
                f"   Quantity: {qty}",
                f"   Supplier: {sup}",
                f"   Amount USD: {row.get('Amount USD', '-')} | Amount LBP: {row.get('Amount LBP', '-')}",
                "",
            ]
        )
    return "\n".join(lines).strip()


async def _handle_plate_info_flow(db: Session, tg_id: str, chat_id: str, text_value: str, state: dict) -> bool:
    if state.get("flow") != "get_information_by_plate":
        return False

    step = state.get("step", "await_plate")
    normalized = text_value.strip()

    if normalized.lower() in {"/cancel", "cancel", "stop"}:
        _clear_flow_state(db, tg_id)
        await send_telegram_message(db, chat_id, "Flow canceled. Send /flow_plate to start again.")
        return True

    if step == "await_plate":
        if not normalized:
            await send_telegram_message(db, chat_id, "Please enter a plate number (example: 104682).")
            return True
        # Prevent accidental menu-key/short inputs from being treated as real plate numbers.
        if not re.fullmatch(r"\d{4,10}", normalized):
            await send_telegram_message(
                db,
                chat_id,
                "Please enter a valid plate number (4-10 digits), example: 104682.",
            )
            return True
        candidate_plate = _extract_plate_candidate(normalized) or normalized
        exists = bool(read_fleet_history_by_plate(db, candidate_plate, limit=1))
        if not exists:
            await send_telegram_message(
                db,
                chat_id,
                f"Plate `{candidate_plate}` was not found. Please send a valid plate number.",
                parse_mode="Markdown",
            )
            return True
        state["plate_number"] = candidate_plate
        state["step"] = "await_order"
        _save_flow_state(db, tg_id, state)
        await send_telegram_message(
            db,
            chat_id,
            "Choose records order:\n`1` New to old\n`2` Old to new",
            parse_mode="Markdown",
        )
        return True

    if step == "await_order":
        newest_first = True
        if normalized in {"1", "new", "new to old", "newest", "desc"}:
            newest_first = True
        elif normalized in {"2", "old", "old to new", "oldest", "asc"}:
            newest_first = False
        state["newest_first"] = newest_first
        state["step"] = "await_description_input"
        _save_flow_state(db, tg_id, state)
        await send_telegram_message(
            db,
            chat_id,
            (
                "Send item description filter text, or send `NO` to continue without description."
            ),
            parse_mode="Markdown",
        )
        return True

    if step == "await_description_input":
        state["item_search"] = "" if normalized.lower() == "no" else normalized
        state["step"] = "show_results"
        step = "show_results"
    elif step == "await_more_choice":
        if normalized in {"1", "more", "next", "yes", "y"}:
            state["step"] = "show_results"
            step = "show_results"
        else:
            state["step"] = "await_email_choice"
            step = "await_email_choice"
            _save_flow_state(db, tg_id, state)
            await send_telegram_message(
                db,
                chat_id,
                "Do you want this report by email? Reply `yes` or `no`.",
                parse_mode="Markdown",
            )
            return True
    elif step == "await_email_choice":
        if not normalized:
            await send_telegram_message(
                db,
                chat_id,
                "Do you want this report by email? Reply `yes` or `no`.",
                parse_mode="Markdown",
            )
            return True
        if _wants_yes(normalized):
            state["step"] = "await_email_address"
            _save_flow_state(db, tg_id, state)
            await send_telegram_message(db, chat_id, "Please provide the recipient email address.")
            return True
        if _wants_no(normalized):
            _clear_flow_state(db, tg_id)
            await send_telegram_message(db, chat_id, "Done. Send /flow_plate anytime to run another query.")
            return True
        await send_telegram_message(
            db,
            chat_id,
            "Please reply `yes` or `no`.",
            parse_mode="Markdown",
        )
        return True
    elif step == "await_email_address":
        recipient_email = normalized
        shown_count = max(int(state.get("offset", 0) or 0), 10)
        rows = read_fleet_history_filtered_page(
            db,
            plate_number=str(state.get("plate_number", "")),
            item_search=str(state.get("item_search", "")),
            offset=0,
            page_size=shown_count,
            newest_first=bool(state.get("newest_first", True)),
        )
        ok, message = _send_fleet_report_email(
            db,
            recipient_email=recipient_email,
            plate_number=str(state.get("plate_number", "")),
            newest_first=bool(state.get("newest_first", True)),
            description_filter=str(state.get("item_search", "")),
            rows=rows,
        )
        _clear_flow_state(db, tg_id)
        if ok:
            await send_telegram_message(
                db,
                chat_id,
                f"{message}\nIncluded records in email: {len(rows)} (already shown in chat).",
            )
        else:
            await send_telegram_message(db, chat_id, f"Email send failed: {message}")
        return True

    if step == "show_results":
        offset = int(state.get("offset", 0) or 0)
        total_count = int(
            state.get("total_count", 0)
            or count_fleet_history_filtered(
                db,
                plate_number=str(state.get("plate_number", "")),
                item_search=str(state.get("item_search", "")),
            )
        )
        state["total_count"] = total_count
        rows = read_fleet_history_filtered_page(
            db,
            plate_number=str(state.get("plate_number", "")),
            item_search=str(state.get("item_search", "")),
            offset=offset,
            page_size=10,
            newest_first=bool(state.get("newest_first", True)),
        )
        if not rows:
            await send_telegram_message(
                db,
                chat_id,
                f"No records found for this query.\nTotal records found: {total_count}",
            )
            state["step"] = "await_email_choice"
            _save_flow_state(db, tg_id, state)
            await send_telegram_message(
                db,
                chat_id,
                "Do you want this report by email? Reply yes or no.",
            )
            return True

        new_offset = offset + len(rows)
        state["offset"] = new_offset
        body = _format_fleet_rows_markdown(rows, start_index=offset + 1)
        await send_telegram_message(
            db,
            chat_id,
            f"{body}\n\nTotal records found: {total_count}\nShown: {new_offset} of {total_count}",
        )
        if new_offset < total_count:
            state["step"] = "await_more_choice"
            _save_flow_state(db, tg_id, state)
            await send_telegram_message(
                db,
                chat_id,
                "Press `1` to load 10 more records, or `2` to continue to email option.",
                parse_mode="Markdown",
            )
            return True

        state["step"] = "await_email_choice"
        _save_flow_state(db, tg_id, state)
        await send_telegram_message(
            db,
            chat_id,
            "These are all the records.\nDo you want this report by email? Reply `yes` or `no`.",
            parse_mode="Markdown",
        )
        return True

    return False


async def handle_message(db: Session, message: dict, source: str = "webhook") -> None:
    from_user = message.get("from", {})
    tg_id = str(from_user.get("id", ""))
    username = from_user.get("username", "")
    full_name = f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip()
    chat_id = str(message.get("chat", {}).get("id", ""))
    incoming_text = (message.get("text") or "").strip()
    message_id = str(message.get("message_id", "")).strip()
    if not (tg_id and chat_id):
        return
    if message_id:
        last_message_id = get_setting(db, _last_message_key(chat_id), "")
        if message_id == last_message_id:
            return
        set_setting(db, _last_message_key(chat_id), message_id)
    log_incoming_message(db, tg_id, username, full_name, chat_id, incoming_text, source=source)

    user = ensure_telegram_user(db, tg_id, username, full_name)
    if not user.is_approved:
        await send_telegram_message(
            db,
            chat_id,
            "Your account is pending admin approval. Please wait.",
        )
        return

    try:
        await _handle_user_message(db, tg_id, chat_id, incoming_text)
    except Exception:
        logging.exception("Telegram handle_message failed for chat_id=%s", chat_id)
        try:
            await send_telegram_message(
                db,
                chat_id,
                "Something went wrong. Send /start to return to the menu, or /cancel to reset a stuck flow.",
            )
        except Exception:
            pass


async def _handle_user_message(db: Session, tg_id: str, chat_id: str, incoming_text: str) -> None:
    normalized = incoming_text.strip()
    normalized_lower = normalized.lower()

    # Be forgiving: users often type "start" without the slash.
    if normalized_lower in {"/start", "/help", "start", "menu", "help"}:
        _save_flow_state(db, tg_id, {"flow": "main_menu", "step": "await_menu_choice"})
        await _send_main_menu(db, chat_id)
        return

    # Also accept natural-language ways of starting the plate flow.
    if normalized_lower in {
        "/flow_plate",
        "/plate_flow",
        "get information by plate",
        "plate flow",
        "start plate flow",
        "start flow",
        "start the flow",
    }:
        state = {"flow": "get_information_by_plate", "step": "await_plate"}
        _save_flow_state(db, tg_id, state)
        await send_telegram_message(
            db,
            chat_id,
            "Flow started: *get information by plate*.\nPlease enter the plate number (example: `104682`).",
            parse_mode="Markdown",
        )
        return

    existing_state = _get_flow_state(db, tg_id)
    if existing_state:
        if existing_state.get("flow") == "main_menu" and existing_state.get("step") == "await_menu_choice":
            if normalized == "1":
                state = {"flow": "get_information_by_plate", "step": "await_plate"}
                _save_flow_state(db, tg_id, state)
                await send_telegram_message(
                    db,
                    chat_id,
                    "Please send the plate number (example: `104682`).",
                    parse_mode="Markdown",
                )
                return
            await _send_main_menu(db, chat_id)
            return
        if await _handle_plate_info_flow(db, tg_id, chat_id, normalized, existing_state):
            return

    if normalized.startswith("/ai_status"):
        ai_url = get_setting(db, "ai_api_url")
        ai_key = get_setting(db, "ai_api_key")
        ai_model = get_setting(db, "ai_model")
        key_state = "set" if ai_key else "missing"
        status_msg = (
            "*AI Configuration Status*\n"
            f"- URL: `{ai_url or 'missing'}`\n"
            f"- API Key: `{key_state}`\n"
            f"- Model: `{ai_model or 'missing'}`\n"
            "Configure these values in `/admin/settings`."
        )
        await send_telegram_message(db, chat_id, status_msg, parse_mode="Markdown")
        return

    if normalized == "1":
        state = {"flow": "get_information_by_plate", "step": "await_plate"}
        _save_flow_state(db, tg_id, state)
        await send_telegram_message(
            db,
            chat_id,
            "Please send the plate number (example: `104682`).",
            parse_mode="Markdown",
        )
        return

    if normalized.startswith("/jobs"):
        rows = read_job_orders(db, limit=5)
        msg = "\n".join([f"#{r['JobID']} | {r['JobCustName']} | {r['JobDate']}" for r in rows]) or "No jobs found."
        await send_telegram_message(db, chat_id, msg, parse_mode="Markdown")
        return

    if normalized.startswith("/plate "):
        plate = normalized.replace("/plate ", "", 1).strip()
        rows = read_fleet_history_by_plate(db, plate, limit=5)
        if not rows:
            await send_telegram_message(db, chat_id, "No history found for this plate.")
            return
        await send_telegram_message(db, chat_id, _format_fleet_rows_markdown(rows))
        return

    # Default behavior: do not use the LLM for arbitrary chat.
    # If the user isn't in a flow and didn't issue a known command, show the main menu.
    if not normalized.startswith("/ai "):
        _save_flow_state(db, tg_id, {"flow": "main_menu", "step": "await_menu_choice"})
        await _send_main_menu(db, chat_id)
        return

    # LLM is only available behind an explicit command.
    incoming_text = normalized.replace("/ai", "", 1).strip()

    ai_url = get_setting(db, "ai_api_url")
    ai_key = get_setting(db, "ai_api_key")
    ai_model = get_setting(db, "ai_model")
    if ai_url and ai_key and ai_model:
        try:
            if _is_openai_chat_endpoint(ai_url):
                request_payload: dict[str, object] = {
                    "model": ai_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "أنت مساعد نظام صيانة الأسطول. "
                                "أجب باللغة العربية وباختصار وبنص عادي."
                            ),
                        },
                        {"role": "user", "content": incoming_text},
                    ],
                }
            else:
                request_payload = {"model": ai_model, "prompt": incoming_text}
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    ai_url,
                    json=request_payload,
                    headers={"Authorization": f"Bearer {ai_key}"},
                )
                response.raise_for_status()
                content = _extract_ai_text(response)[:3500]
                log_ai_attempt(
                    db,
                    tg_id=tg_id,
                    chat_id=chat_id,
                    input_text=incoming_text,
                    ai_url=ai_url,
                    ai_model=ai_model,
                    status="success",
                    response_preview=content,
                )
                await send_telegram_message(db, chat_id, content or "AI returned empty response.")
                return
        except Exception as exc:
            error_text = str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_text = f"{exc} | body={exc.response.text[:1500]}"
                except Exception:
                    error_text = str(exc)
            log_ai_attempt(
                db,
                tg_id=tg_id,
                chat_id=chat_id,
                input_text=incoming_text,
                ai_url=ai_url,
                ai_model=ai_model,
                status="failed",
                error_message=error_text,
            )
            await send_telegram_message(
                db,
                chat_id,
                "AI request failed (logged). Use /start for the menu or /ai_status for configuration.",
            )
            return

    _save_flow_state(db, tg_id, {"flow": "main_menu", "step": "await_menu_choice"})
    await _send_main_menu(db, chat_id)

