@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  echo This helper does NOT update or install Norm.
  echo It only regenerates the .sha256 checksum beside an intentionally rebuilt source ZIP.
  echo.
  echo Drag a rebuilt Norm portable-source ZIP onto this BAT file.
  echo.
  echo Example:
  echo   Update-Source-SHA.bat Norm-0.52.2-portable-source.zip
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-Source-SHA.ps1" "%~1"
if errorlevel 1 (
  echo.
  echo Checksum update failed.
  pause
  exit /b 1
)

echo.
echo Checksum regenerated successfully.
pause
exit /b 0
