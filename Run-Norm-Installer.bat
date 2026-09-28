@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
  py -3.14 "%~dp0Norm-Installer.py"
  if not errorlevel 1 exit /b 0
  py -3 "%~dp0Norm-Installer.py"
  if not errorlevel 1 exit /b 0
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0Norm-Installer.py"
  exit /b %errorlevel%
)

echo Python was not found. Use the compiled Norm-Installer-1.3.8.exe or install Python.
pause
exit /b 1
