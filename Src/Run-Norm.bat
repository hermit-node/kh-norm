@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "NORM_EXE=%ROOT%core\norm.exe"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "SETTINGS=%ROOT%config\settings.ini"

if not exist "%NORM_EXE%" (
  echo ERROR: Norm executable not found: %NORM_EXE%
  pause
  exit /b 1
)
if not exist "%PY%" (
  echo ERROR: Norm Python environment not found: %PY%
  pause
  exit /b 1
)
if not exist "%SETTINGS%" (
  echo ERROR: Norm settings not found: %SETTINGS%
  pause
  exit /b 1
)

for /f "usebackq delims=" %%I in (`tailscale ip -4 2^>nul`) do if not defined TS_IP set "TS_IP=%%I"
if not defined TS_IP (
  echo ERROR: Tailscale is not running. Norm is configured fail-closed.
  pause
  exit /b 1
)

for /f "tokens=1,2" %%A in ('%PY% -c "from pathlib import Path; import sys; root=Path(r'%SETTINGS%').parents[1]; sys.path.insert(0,str(root/'core')); from norm_runtime.settings import load_ports; p=load_ports(root); print(p['activity'],p['norm_http'])"') do (
  set "ACTIVITY_PORT=%%A"
  set "NORM_PORT=%%B"
)
if not defined ACTIVITY_PORT (
  echo ERROR: Could not resolve activity port from config\settings.ini.
  pause
  exit /b 1
)
if not defined NORM_PORT (
  echo ERROR: Could not resolve Norm HTTP port from config\settings.ini.
  pause
  exit /b 1
)

set "CHAT_HEALTH=http://%TS_IP%:%NORM_PORT%/health"
set "ACTIVITY_HEALTH=http://%TS_IP%:%ACTIVITY_PORT%/health"

curl.exe -fsS "%CHAT_HEALTH%" >NUL 2>&1
if errorlevel 1 goto start_service
curl.exe -fsS "%ACTIVITY_HEALTH%" >NUL 2>&1
if not errorlevel 1 goto ready

:start_service
echo Starting or attaching to the Norm service...
"%PY%" "%ROOT%tools\start_norm_service.py"
set "START_RC=%ERRORLEVEL%"
if "%START_RC%"=="2" (
  echo Existing norm.exe is still present. Waiting for its APIs instead of launching a duplicate...
) else if not "%START_RC%"=="0" (
  echo ERROR: Could not launch Norm service helper. Exit code %START_RC%.
  pause
  exit /b %START_RC%
)
for /L %%I in (1,1,90) do (
  curl.exe -fsS "%CHAT_HEALTH%" >NUL 2>&1 && curl.exe -fsS "%ACTIVITY_HEALTH%" >NUL 2>&1 && goto ready
  ping.exe -n 2 127.0.0.1 >NUL
)
echo ERROR: Norm did not become healthy within the startup window.
curl.exe -fsS "%CHAT_HEALTH%" >NUL 2>&1
if errorlevel 1 (echo   Chat API:     NOT HEALTHY  %CHAT_HEALTH%) else (echo   Chat API:     healthy)
curl.exe -fsS "%ACTIVITY_HEALTH%" >NUL 2>&1
if errorlevel 1 (echo   Activity API: NOT HEALTHY  %ACTIVITY_HEALTH%) else (echo   Activity API: healthy)
echo.
echo norm.exe process inventory:
tasklist /FI "IMAGENAME eq norm.exe" /FO TABLE
echo.
echo Recent startup diagnostics:
for %%L in (norm-bootstrap.stderr.log norm-bootstrap.stdout.log norm-runtime.log server.stderr.log) do (
  if exist "%ROOT%logs\%%L" (
    echo ===== %%L =====
    powershell.exe -NoProfile -Command "Get-Content -LiteralPath '%ROOT%logs\%%L' -Tail 30 -ErrorAction SilentlyContinue"
  )
)
echo.
echo A stale norm.exe can block a replacement even when both APIs are dead.
if not defined NORM_RECOVERY_ATTEMPTED (
  choice /C YN /N /M "Terminate any stale norm.exe and retry once? [Y/N] "
  if errorlevel 2 goto startup_failed
  set "NORM_RECOVERY_ATTEMPTED=1"
  taskkill /IM norm.exe /F >NUL 2>&1
  ping.exe -n 3 127.0.0.1 >NUL
  goto start_service
)

:startup_failed
echo To test the runtime visibly, open PowerShell and run:  cd C:\Norm; .\core\norm.exe
pause
exit /b 1

:ready
echo Norm is healthy at %CHAT_HEALTH%
if /I "%~1"=="--service-only" exit /b 0

pushd "%ROOT%"
"%PY%" "%ROOT%tools\start_operator_consoles.py"
set "CONSOLE_RC=%ERRORLEVEL%"
popd
if not "%CONSOLE_RC%"=="0" (
  echo ERROR: One or more Norm operator consoles could not be launched. Exit code %CONSOLE_RC%.
  pause
  exit /b %CONSOLE_RC%
)

endlocal
exit /b 0
