@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON="
if not "%~1"=="" if exist "%~1" set "PYTHON=%~1"
if not defined PYTHON if exist "C:\Python314\python.exe" set "PYTHON=C:\Python314\python.exe"

if not defined PYTHON (
  where py >nul 2>&1
  if not errorlevel 1 (
    for /f "usebackq delims=" %%P in (`py -3.14 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON=%%P"
  )
)

if not defined PYTHON (
  where python >nul 2>&1
  if not errorlevel 1 for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)"`) do set "PYTHON=%%P"
)

if not defined PYTHON (
  echo Python was not found.
  echo You can also drag python.exe onto this BAT or run:
  echo   Build-Norm-Installer-EXE.bat C:\path\to\python.exe
  pause
  exit /b 1
)

set "BUILDROOT=%TEMP%\norm-installer-build-%RANDOM%-%RANDOM%"
set "VENV=%BUILDROOT%\venv"
mkdir "%BUILDROOT%" >nul 2>&1

echo Using Python: %PYTHON%
"%PYTHON%" -m venv "%VENV%"
if errorlevel 1 goto :fail

"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller==6.22.2
if errorlevel 1 goto :fail

"%VENV%\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name Norm-Installer ^
  --distpath "%BUILDROOT%\dist" ^
  --workpath "%BUILDROOT%\work" ^
  --specpath "%BUILDROOT%\spec" ^
  "%~dp0Norm-Installer.py"
if errorlevel 1 goto :fail

copy /y "%BUILDROOT%\dist\Norm-Installer.exe" "%~dp0Norm-Installer.exe" >nul
if errorlevel 1 goto :fail

rmdir /s /q "%BUILDROOT%" >nul 2>&1
echo.
echo Built: %~dp0Norm-Installer.exe
echo Keep any Norm portable-source ZIP beside the EXE or browse to one in the GUI.
pause
exit /b 0

:fail
echo.
echo Build failed. Temporary build files were left at:
echo   %BUILDROOT%
pause
exit /b 1
