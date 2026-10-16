@echo off
setlocal
set "NORM_ROOT=%~dp0..\.."
set "PYTHON=%NORM_ROOT%\.venv\Scripts\pythonw.exe"
if not exist "%PYTHON%" set "PYTHON=%NORM_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Missing Norm-local Python
  pause
  exit /b 1
)
start "" "%PYTHON%" -B "%~dp0app.py" %*
