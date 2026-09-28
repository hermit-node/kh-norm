@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Make Norm Installer 1.4.2

if not "%~1"=="" if exist "%~1" (
  "%~1" "%~dp0Make-Norm-Installer.py"
  exit /b %errorlevel%
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3.14 "%~dp0Make-Norm-Installer.py"
  if not errorlevel 1 exit /b 0
  py -3 "%~dp0Make-Norm-Installer.py"
  if not errorlevel 1 exit /b 0
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0Make-Norm-Installer.py"
  exit /b %errorlevel%
)

echo Python was not found.
echo.
echo Install Python or drag a python.exe onto this BAT.
pause
exit /b 1
