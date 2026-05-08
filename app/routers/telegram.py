from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.telegram_service import handle_message

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    message = payload.get("message")
    if message:
        await handle_message(db, message)
    return JSONResponse({"ok": True})

