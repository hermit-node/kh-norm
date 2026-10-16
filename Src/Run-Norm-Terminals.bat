@echo off
setlocal EnableExtensions DisableDelayedExpansion
REM Independent Runtime, Replies, and Prompt windows. No Qt Focus container.
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "BOOT=%ROOT%Run-Norm.bat"
set "ROLES=%ROOT%tools\start_operator_consoles.py"
if /I "%~1"=="--check" goto check
if not "%~1"=="" goto usage
call :check
if errorlevel 1 exit /b 2
echo Starting or reusing Norm backend and Celery workers...
call "%BOOT%" --service-only
if errorlevel 1 (
  echo ERROR: Norm backend startup failed.
  exit /b 1
)
echo Starting or reusing independent Runtime, Replies, and Prompt terminals...
"%PY%" -B "%ROLES%"
exit /b %ERRORLEVEL%
:check
if not exist "%PY%" (echo ERROR: Python runtime missing & exit /b 2)
if not exist "%BOOT%" (echo ERROR: Backend BAT missing & exit /b 2)
if not exist "%ROLES%" (echo ERROR: Terminal role launcher missing & exit /b 2)
echo OK: Backend and independent terminal-launcher paths verified.
exit /b 0
:usage
echo Usage: %~nx0 [--check]
exit /b 2
