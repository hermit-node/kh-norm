@echo off
setlocal
set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0fetch_weasyprint_runtime.py" --quiet
if errorlevel 1 (
  echo Norm could not prepare the verified WeasyPrint/Pango runtime. 1>&2
  exit /b 2
)
"%~dp0weasyprint\runtime\weasyprint.exe" %*
exit /b %errorlevel%
