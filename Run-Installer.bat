@echo off
setlocal
cd /d "%~dp0"
set "SOURCE=%~dp0Norm-0.53.20-portable-source.zip"
if exist "%~dp0dist\Norm-Installer.exe" (
  "%~dp0dist\Norm-Installer.exe" --source "%SOURCE%"
  exit /b %errorlevel%
)
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 "%~dp0Norm-Installer.py" --source "%SOURCE%"
) else (
  python "%~dp0Norm-Installer.py" --source "%SOURCE%"
)
exit /b %errorlevel%
