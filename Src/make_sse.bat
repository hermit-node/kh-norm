@echo off
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: Norm virtual environment Python was not found:
  echo   %PY%
  echo Run the Norm installer first, then run make_sse.bat again.
  exit /b 1
)

if not exist "%~dp0tools\build_sse_client.py" (
  echo ERROR: Missing tools\build_sse_client.py
  exit /b 1
)

"%PY%" "%~dp0tools\build_sse_client.py" %*
exit /b %ERRORLEVEL%
