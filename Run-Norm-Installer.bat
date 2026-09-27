@echo off
setlocal
cd /d "%~dp0"

if exist "C:\Python314\python.exe" (
  "C:\Python314\python.exe" "%~dp0Norm-Installer.py"
  exit /b %errorlevel%
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3.14 "%~dp0Norm-Installer.py" >nul 2>&1
  if not errorlevel 1 exit /b 0
  py -3 "%~dp0Norm-Installer.py"
  exit /b %errorlevel%
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0Norm-Installer.py"
  exit /b %errorlevel%
)

echo.
echo Python was not found. Install Python or run Norm-Installer.py with an existing Python interpreter.
pause
exit /b 1
