@echo off
setlocal EnableExtensions
set "NORM_ROOT=%~dp0"
set "PYTHON=%NORM_ROOT%.venv\\Scripts\\python.exe"
if not exist "%PYTHON%" (
  echo ERROR: Missing Norm-local Python at "%PYTHON%"
  pause
  exit /b 1
)
"%PYTHON%" -B "%NORM_ROOT%tools\\show_shared_browser.py"
if errorlevel 1 (
  echo Shared browser could not be shown. Check Norm logs and install state.
  pause
  exit /b 1
)
endlocal
