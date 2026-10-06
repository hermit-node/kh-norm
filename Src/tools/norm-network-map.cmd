@echo off
setlocal
set "ROOT=%~dp0.."
if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\tools\norm_network_map.py" %*
  exit /b %errorlevel%
)
py -3 "%ROOT%\tools\norm_network_map.py" %*
exit /b %errorlevel%
