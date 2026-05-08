from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import BootstrapConfig, load_bootstrap
from app.services.setup_service import initialize_database, save_connection_settings

router = APIRouter(tags=["setup"])
templates = Jinja2Templates(directory="templates")


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    return templates.TemplateResponse(
        request,
        "setup.html",
        {"cfg": load_bootstrap(), "message": ""},
    )


@router.post("/setup", response_class=HTMLResponse)
def save_setup(
    request: Request,
    db_host: str = Form(...),
    db_name: str = Form(...),
    db_user: str = Form(...),
    db_password: str = Form(...),
    db_driver: str = Form("ODBC Driver 17 for SQL Server"),
    ad_server: str = Form(""),
    ad_domain: str = Form(""),
    telegram_bot_token: str = Form(""),
    ai_api_url: str = Form(""),
    ai_api_key: str = Form(""),
    ai_model: str = Form(""),
    dev_mode: bool = Form(False),
    dev_admin_user: str = Form("admin"),
    dev_admin_password: str = Form("admin123"),
):
    cfg = BootstrapConfig(
        db_host=db_host,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_driver=db_driver,
        ad_server=ad_server,
        ad_domain=ad_domain,
        telegram_bot_token=telegram_bot_token,
        ai_api_url=ai_api_url,
        ai_api_key=ai_api_key,
        ai_model=ai_model,
        dev_mode=dev_mode,
        dev_admin_user=dev_admin_user,
        dev_admin_password=dev_admin_password,
    )
    save_connection_settings(cfg)

    try:
        initialize_database(cfg)
        return RedirectResponse("/login", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"cfg": cfg, "message": f"Setup failed: {exc}"},
        )

