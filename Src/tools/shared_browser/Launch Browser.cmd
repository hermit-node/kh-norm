@echo off
setlocal
set "NORM_ROOT=%~dp0..\.."
set "PYTHON=%NORM_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Missing Norm-local Python: "%PYTHON%"
  pause
  exit /b 1
)
"%PYTHON%" -B "%~dp0app.py" --show %*
if errorlevel 1 pause
