@echo off
setlocal
cd /d "%~dp0"
set "PY=py -3.14"
%PY% -m pip install --upgrade "pip==26.2.1" "pyinstaller==6.22.3"
if errorlevel 1 exit /b %errorlevel%
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name "Norm-Installer" "%~dp0Norm-Installer.py"
if errorlevel 1 exit /b %errorlevel%
copy /y "%~dp0dist\Norm-Installer.exe" "%~dp0Norm-Installer.exe" >nul
echo Built: %~dp0Norm-Installer.exe
