from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session

from app.database import get_db
from app.security import authenticate, login_user, logout_user

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        if authenticate(db, username, password):
            login_user(request, username)
            return RedirectResponse("/admin", status_code=303)
        raise HTTPException(status_code=401, detail="Invalid")
    except HTTPException:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid login. Check credentials or AD connectivity."},
            status_code=401,
        )


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)

