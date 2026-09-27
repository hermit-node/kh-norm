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
echo Starting Norm service and Ollama...
start "Norm Service" /min "%NORM_EXE%" --service
for /L %%I in (1,1,90) do (
  curl.exe -fsS "%CHAT_HEALTH%" >NUL 2>&1 && curl.exe -fsS "%ACTIVITY_HEALTH%" >NUL 2>&1 && goto ready
  ping.exe -n 2 127.0.0.1 >NUL
)
echo ERROR: Norm did not become healthy.
echo Check: %ROOT%logs\norm-runtime.log
pause
exit /b 1

:ready
echo Norm is healthy at %CHAT_HEALTH%
if /I "%~1"=="--service-only" exit /b 0

pushd "%ROOT%"
start "Norm Runtime" "%PY%" -u "%ROOT%tools\norm_gui_stream.py"
start "Norm Replies" "%PY%" -u "%ROOT%tools\norm_gui_reply.py"
start "Norm Prompt" "%PY%" -u "%ROOT%tools\norm_gui_prompt.py"
popd

endlocal
exit /b 0
