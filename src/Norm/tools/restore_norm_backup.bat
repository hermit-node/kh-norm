@echo off
setlocal
set "ZIP=%~1"
if "%ZIP%"=="" (
    set /p "ZIP=Path to Norm backup ZIP: "
)
if "%ZIP%"=="" (
    echo No backup ZIP specified.
    exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore_norm_backup.ps1" -ZipPath "%ZIP%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Restore exited with code %RC%.
exit /b %RC%
