@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 Build-Installer.py %*
) else (
  python Build-Installer.py %*
)
exit /b %errorlevel%
