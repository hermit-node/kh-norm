@echo off
setlocal EnableExtensions
set "ROOT=%~dp0..\\.."
set "PYTHON=%ROOT%\\.venv\\Scripts\\python.exe"
if not exist "%PYTHON%" (
 echo Missing Norm-local Python: "%PYTHON%"
 pause
 exit /b 1
)
"%PYTHON%" -B "%ROOT%\\tools\\start_shared_browser.py"
if errorlevel 1 pause
