# Maintenance Hub Portal (FastAPI)

Modern FastAPI portal for maintenance operations with:
- AD-aware admin login (plus local dev full-admin mode)
- SQL-backed system settings
- Fail-safe setup page that works before DB exists
- Telegram user approval workflow and read-only commands
- Optional AI API fallback configuration page
- Read-only SQL access to maintenance operational tables

## Run

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Start:
   - `uvicorn app.main:app --reload`
3. Open:
   - `http://127.0.0.1:8000`

### Windows launchers

- `run_portal.bat`:
  - Installs/updates requirements then runs the app in console mode.
- `run_portal_tray.vbs`:
  - Starts the app in the background with a system tray icon.
  - Tray menu provides:
    - `Open Web`
    - `Exit`

## First Time Setup

- Open `/setup`
- Provide SQL Server host, DB name, username, password, driver
- Optional: AD server/domain, Telegram token, AI settings
- Save to create internal tables and read-only views

## Telegram Commands

- `/jobs`
- `/plate <PLATE_NUMBER>`

Admin must approve Telegram users from `/admin`.

## Important

- Business data operations are read-only (`SELECT` only).
- DB bootstrap creates internal config/approval tables and read-only views.

