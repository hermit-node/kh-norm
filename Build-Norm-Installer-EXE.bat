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

set "PIP_SPEC_FILE=%BUILDROOT%\pip-spec.txt"
"%PYTHON%" "%~dp0Norm-Installer.py" --print-pip-spec > "%PIP_SPEC_FILE%"
if errorlevel 1 goto :fail
set /p "PIP_SPEC=" < "%PIP_SPEC_FILE%"
if not defined PIP_SPEC goto :fail
"%VENV%\Scripts\python.exe" -m pip install --upgrade "%PIP_SPEC%"
if errorlevel 1 goto :fail

"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller==6.22.3
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

set "SMOKE_FILE=%BUILDROOT%\installer-self-test.txt"
echo Smoke-testing generated Norm-Installer.exe...
"%BUILDROOT%\dist\Norm-Installer.exe" --self-test-file "%SMOKE_FILE%"
if errorlevel 1 goto :fail
if not exist "%SMOKE_FILE%" (
  echo ERROR: Generated Norm-Installer.exe did not complete its startup self-test.
  goto :fail
)
findstr /b /c:"ok 1.3.6" "%SMOKE_FILE%" >nul
if errorlevel 1 (
  echo ERROR: Generated Norm-Installer.exe returned an invalid self-test result.
  goto :fail
)

copy /y "%BUILDROOT%\dist\Norm-Installer.exe" "%~dp0Norm-Installer.exe" >nul
if errorlevel 1 goto :fail

rmdir /s /q "%BUILDROOT%" >nul 2>&1
echo.
echo Built: %~dp0Norm-Installer.exe
echo Keep any Norm portable-source or full-backup ZIP beside the EXE or browse to one in the GUI.
pause
exit /b 0

:fail
echo.
echo Build failed. Temporary build files were left at:
echo   %BUILDROOT%
pause
exit /b 1
