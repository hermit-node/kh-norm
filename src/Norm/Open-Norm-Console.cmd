@echo off
setlocal
set "SCRIPT=%~dp0Open-Norm-Console.ps1"
powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File "%SCRIPT%"
endlocal
