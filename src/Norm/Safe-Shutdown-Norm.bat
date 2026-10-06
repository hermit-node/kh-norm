@echo off
setlocal
set "ROOT=%~dp0"
set "SETTINGS_FILE=%ROOT%config\settings.ini"

if not exist "%SETTINGS_FILE%" (
    echo Norm settings not found: %SETTINGS_FILE%
    exit /b 1
)

for /f "tokens=1,2" %%A in ('powershell.exe -NoLogo -NoProfile -Command "$s=@{};$section='';foreach($raw in [IO.File]::ReadAllLines('%SETTINGS_FILE%')){$l=$raw.Trim();if($l -match '^\[(.+)\]$'){$section=$matches[1].Trim()}elseif($section -eq 'network' -and $l -match '^([^=]+)=(.*)$'){$s[$matches[1].Trim()]=$matches[2].Trim()}};'{0} {1}' -f $s['activity_port'],$s['norm_port']"') do (
    set "ACTIVITY_PORT=%%A"
    set "NORM_PORT=%%B"
)

if not defined ACTIVITY_PORT (
    echo Could not read the Norm activity port from settings.ini.
    exit /b 1
)

for /f "usebackq delims=" %%I in (`tailscale ip -4 2^>nul`) do if not defined TAILSCALE_IP set "TAILSCALE_IP=%%I"
if not defined TAILSCALE_IP (
    echo Tailscale is unavailable. Norm is configured fail-closed on the tailnet.
    exit /b 1
)
set "SHUTDOWN_URL=http://%TAILSCALE_IP%:%ACTIVITY_PORT%/control/shutdown-norm"
if /I "%~1"=="--dry-run" (
    echo Graceful shutdown endpoint: %SHUTDOWN_URL%
    exit /b 0
)

echo Requesting graceful Norm shutdown...
curl.exe -fsS -X POST "%SHUTDOWN_URL%"
if errorlevel 1 (
    echo.
    echo Could not contact Norm through Tailscale. It may already be stopped.
    exit /b 1
)

echo.
echo Shutdown accepted. Norm will stop accepting new work, drain queued work,
echo purge queued deletions, clear runtime Redis, and then exit cleanly.
endlocal
