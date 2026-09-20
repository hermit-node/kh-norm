@echo off
setlocal
set "ROOT=%~dp0"
set "NORM_EXE=%ROOT%app\norm.exe"
set "SETTINGS_FILE=%ROOT%config\settings.ini"

if not exist "%NORM_EXE%" (
    echo Norm executable not found: %NORM_EXE%
    exit /b 1
)
if not exist "%SETTINGS_FILE%" (
    echo Norm settings not found: %SETTINGS_FILE%
    exit /b 1
)
for /f "tokens=1,2,3" %%A in ('powershell.exe -NoLogo -NoProfile -Command "$s=@{};$section='';foreach($raw in [IO.File]::ReadAllLines('%SETTINGS_FILE%')){$l=$raw.Trim();if($l -match '^\[(.+)\]$'){$section=$matches[1].Trim()}elseif($section -eq 'ports' -and $l -match '^([^=]+)=(.*)$'){$s[$matches[1].Trim()]=$matches[2].Trim()}};'{0} {1} {2}' -f $s['ollama'],$s['norm_http'],$s['activity']"') do (
    set "OLLAMA_PORT=%%A"
    set "NORM_PORT=%%B"
    set "ACTIVITY_PORT=%%C"
)
if not defined NORM_PORT exit /b 1

echo Norm ports: Ollama=%OLLAMA_PORT% Norm=%NORM_PORT% Activity=%ACTIVITY_PORT%
curl.exe -fsS http://127.0.0.1:%NORM_PORT%/health >NUL 2>&1
if errorlevel 1 start "Norm Service" /min "%NORM_EXE%"
for /L %%I in (1,1,30) do (
    curl.exe -fsS http://127.0.0.1:%NORM_PORT%/health >NUL 2>&1 && goto service_ready
    timeout.exe /T 1 /NOBREAK >NUL
)
echo Norm service did not become ready.
exit /b 1
:service_ready
start "Norm Console" "%NORM_EXE%" --console
endlocal
