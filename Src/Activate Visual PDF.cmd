@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Missing Norm Python: %PY%
  pause
  exit /b 1
)
"%PY%" -B "%ROOT%tools\restart_shared_browser_visual.py"
if errorlevel 1 pause
