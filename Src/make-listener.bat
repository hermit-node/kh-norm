@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: Norm virtual environment Python not found: "%PY%"
  echo Install Norm first.
  exit /b 1
)
if not exist "%~dp0tools\build_listener.py" (
  echo ERROR: Missing tools\build_listener.py
  exit /b 1
)
"%PY%" "%~dp0tools\build_listener.py" %*
exit /b %ERRORLEVEL%
