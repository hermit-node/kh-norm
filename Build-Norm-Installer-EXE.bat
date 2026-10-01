@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=py -3.14"
set "CACHE=%LOCALAPPDATA%\Norm\InstallerBuilder\public-py3.14"
set "VPY=%CACHE%\Scripts\python.exe"

if not exist "%VPY%" (
  echo Creating reusable installer builder environment...
  %PY% -m venv "%CACHE%"
  if errorlevel 1 exit /b %errorlevel%
)

"%VPY%" -c "import sys; assert sys.version_info[:2] == (3,14)"
if errorlevel 1 (
  echo Cached builder uses the wrong Python version. Remove:
  echo   %CACHE%
  exit /b 2
)

"%VPY%" -c "import importlib.metadata as m; assert m.version('pip') == '26.2.1'; assert m.version('pyinstaller') == '6.22.3'" >nul 2>nul
if errorlevel 1 (
  echo Normalizing reusable builder dependencies...
  "%VPY%" -m pip install --disable-pip-version-check "pip==26.2.1" "pyinstaller==6.22.3"
  if errorlevel 1 exit /b %errorlevel%
)

"%VPY%" -m pip check
if errorlevel 1 exit /b %errorlevel%

"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "Norm-Installer" "%~dp0Norm-Installer.py"
if errorlevel 1 exit /b %errorlevel%

copy /y "%~dp0dist\Norm-Installer.exe" "%~dp0Norm-Installer.exe" >nul
echo Built: %~dp0Norm-Installer.exe
exit /b 0
