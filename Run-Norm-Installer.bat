@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0Norm-Installer.exe" (
  start "" "%~dp0Norm-Installer.exe"
  exit /b 0
)

where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3.14 "%~dp0Norm-Installer.py"
  exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0Norm-Installer.py"
  exit /b 0
)

echo Python 3.14 with Tkinter is required to launch the Norm Installer.
pause
exit /b 1
