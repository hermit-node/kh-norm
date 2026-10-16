@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\\Scripts\\python.exe"
set "HELPER=%ROOT%tools\\safe_shutdown_norm.py"
if not exist "%PY%" (
  echo ERROR: Norm-local Python missing: "%PY%"
  exit /b 1
)
if not exist "%HELPER%" (
  echo ERROR: Shutdown helper missing: "%HELPER%"
  exit /b 1
)
"%PY%" -B "%HELPER%" %*
set "SHUTDOWN_RC=%ERRORLEVEL%"
if not "%SHUTDOWN_RC%"=="0" (
  echo Shutdown verification reported exit code %SHUTDOWN_RC%.
)
endlocal & exit /b %SHUTDOWN_RC%
