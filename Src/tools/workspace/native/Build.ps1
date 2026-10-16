$ErrorActionPreference = 'Stop'
$pf = [Environment]::GetFolderPath('ProgramFilesX86')
$vswhere = Join-Path $pf 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio C++ Build Tools required.' }
$compiler = & $vswhere -all -products '*' -find 'VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe' | Select-Object -Last 1
if (-not $compiler) { $compiler = Get-ChildItem (Join-Path $pf 'Microsoft Visual Studio\2019') -Recurse -Filter cl.exe -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*\bin\Hostx64\x64\cl.exe' } | Select-Object -Last 1 -ExpandProperty FullName }
if (-not $compiler) { throw 'No MSVC x64 compiler found.' }
$msvc = Split-Path (Split-Path (Split-Path (Split-Path $compiler)))
$sdk = Join-Path $pf 'Windows Kits\10'
$sdkVersion = Get-ChildItem -LiteralPath (Join-Path $sdk 'Include') -Directory | Sort-Object Name | Select-Object -Last 1 -ExpandProperty Name
$oldPath=$env:PATH; $oldInclude=$env:INCLUDE; $oldLib=$env:LIB
$env:PATH = "$sdk\bin\$sdkVersion\x64;" + $env:PATH
$env:INCLUDE = "$msvc\include;$sdk\Include\$sdkVersion\ucrt;$sdk\Include\$sdkVersion\shared;$sdk\Include\$sdkVersion\um;$sdk\Include\$sdkVersion\winrt"
$env:LIB = "$msvc\lib\x64;$sdk\Lib\$sdkVersion\ucrt\x64;$sdk\Lib\$sdkVersion\um\x64"
Push-Location $PSScriptRoot
try {
 & $compiler /nologo /std:c++17 /EHsc /W4 /O2 /MT /Fo:NormWorkspace.obj /Fe:NormWorkspace.exe NormWorkspace.cpp /link /SUBSYSTEM:WINDOWS /MANIFEST:EMBED user32.lib gdi32.lib shell32.lib comdlg32.lib
 if ($LASTEXITCODE -ne 0) { throw "Build failed: $LASTEXITCODE" }
 Get-Item '.\NormWorkspace.exe' | Select-Object FullName,Length
} finally { Pop-Location; $env:PATH=$oldPath; $env:INCLUDE=$oldInclude; $env:LIB=$oldLib }

