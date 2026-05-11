@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

echo [Maintenance Hub] Installing/updating requirements...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

set "EXISTING_PORT="
for /f %%p in ('powershell -NoProfile -Command "$proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { ($_.Name -eq ''python.exe'' -or $_.Name -eq ''python'') -and $_.CommandLine -like ''*uvicorn app.main:app*'' }; if($proc){ $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in ($proc.ProcessId) } | Select-Object -ExpandProperty LocalPort -Unique | Sort-Object; if($ports){ $ports | Select-Object -First 1 } }" 2^>nul') do set "EXISTING_PORT=%%p"
echo %EXISTING_PORT%| findstr /r "^[0-9][0-9]*$" >nul || set "EXISTING_PORT="
if defined EXISTING_PORT (
  echo [Maintenance Hub] An instance is already running on port %EXISTING_PORT%.
  echo [Maintenance Hub] Open: http://127.0.0.1:%EXISTING_PORT%
  pause
  exit /b 0
)

for /f %%p in ('powershell -NoProfile -Command "$used=(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort -Unique); $candidate=(8100..8199 | Where-Object { $used -notcontains $_ } | Select-Object -First 1); if(-not $candidate){$candidate=8765}; Write-Output $candidate"') do set "PORT=%%p"

if not defined PORT set "PORT=8765"

for /f %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1 -ExpandProperty IPAddress)"') do set "LAN_IP=%%i"
if not defined LAN_IP set "LAN_IP=YOUR_PC_IP"

netsh advfirewall firewall show rule name="MaintenanceHub_%PORT%" >nul 2>&1
if errorlevel 1 (
  netsh advfirewall firewall add rule name="MaintenanceHub_%PORT%" dir=in action=allow protocol=TCP localport=%PORT% >nul 2>&1
)

echo [Maintenance Hub] Starting FastAPI on all interfaces (0.0.0.0:%PORT%)
echo [Maintenance Hub] Local URL:   http://127.0.0.1:%PORT%
echo [Maintenance Hub] Network URL: http://%LAN_IP%:%PORT%
set "UVICORN_RELOAD_ARG="
if /I "%PORTAL_DEV_RELOAD%"=="1" set "UVICORN_RELOAD_ARG=--reload"
if defined UVICORN_RELOAD_ARG (
  echo [Maintenance Hub] Dev reload mode enabled - PORTAL_DEV_RELOAD=1.
) else (
  echo [Maintenance Hub] Running in single-process mode - recommended.
)
"%PYTHON_EXE%" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT% %UVICORN_RELOAD_ARG%

endlocal
