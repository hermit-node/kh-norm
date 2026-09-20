@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "PROMPT=%ROOT%tools\norm_gui_prompt.py"
set "STREAM=%ROOT%tools\norm_gui_stream.py"
set "REPLY=%ROOT%tools\norm_gui_reply.py"

if not exist "%PY%" (
    echo Norm Python environment not found: %PY%
    exit /b 1
)
if not exist "%PROMPT%" (
    echo Norm GUI prompt helper not found: %PROMPT%
    exit /b 1
)
if not exist "%STREAM%" (
    echo Norm GUI stream helper not found: %STREAM%
    exit /b 1
)
if not exist "%REPLY%" (
    echo Norm GUI reply helper not found: %REPLY%
    exit /b 1
)

if /I "%~1"=="--dry-run" (
    "%PY%" "%PROMPT%" --probe
    exit /b %errorlevel%
)
start "Norm Runtime Stream" cmd.exe /c ""%PY%" "%STREAM%""
start "Norm Replies" cmd.exe /c ""%PY%" "%REPLY%""
start "Norm Prompt" cmd.exe /c ""%PY%" "%PROMPT%""

endlocal
exit /b 0
