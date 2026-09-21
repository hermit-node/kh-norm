param(
    [Parameter(Mandatory=$true)]
    [string]$ZipPath
)

$ErrorActionPreference = 'Stop'
$zip = (Resolve-Path -LiteralPath $ZipPath).Path
$temp = Join-Path $env:TEMP ("norm-restore-" + [guid]::NewGuid().ToString('N'))

function Invoke-RobocopyOverlay([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NP
    $code = $LASTEXITCODE
    if ($code -ge 8) { throw "robocopy failed with exit code $code for $Source -> $Destination" }
}

function Test-Python([string]$Exe, [string]$MajorMinor) {
    if (-not $Exe -or -not (Test-Path $Exe)) { return $false }
    try {
        $v = (& $Exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null).Trim()
        return $v -eq $MajorMinor
    } catch { return $false }
}

function Find-Python([string]$MajorMinor) {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $exe = (& $py.Source "-$MajorMinor" -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and (Test-Python $exe $MajorMinor)) { return $exe.Trim() }
    }
    foreach ($name in @('python.exe','python3.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and (Test-Python $cmd.Source $MajorMinor)) { return $cmd.Source }
    }
    return $null
}

function Install-Python([string]$Version, [string]$MajorMinor) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Python $MajorMinor not found; installing with winget..."
        & $winget.Source install --id "Python.Python.$MajorMinor" --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) {
            $found = Find-Python $MajorMinor
            if ($found) { return $found }
        }
    }
    Write-Host "winget install unavailable/unsuccessful; downloading Python $Version from python.org..."
    $installer = Join-Path $temp "python-$Version-amd64.exe"
    $url = "https://www.python.org/ftp/python/$Version/python-$Version-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $installer
    $proc = Start-Process -FilePath $installer -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_test=0' -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "Python installer failed with exit code $($proc.ExitCode)" }
    $found = Find-Python $MajorMinor
    if ($found) { return $found }
    $compact = $MajorMinor.Replace('.','')
    $candidate = Join-Path $env:LocalAppData "Programs\Python\Python$compact\python.exe"
    if (Test-Python $candidate $MajorMinor) { return $candidate }
    throw "Python $MajorMinor installation completed but python.exe could not be located."
}

function Ensure-NormVenv([string]$BasePython, [string]$VenvPath, [string]$RequirementsFile, [string]$TorchVersion, [string]$TorchIndex) {
    $venvPython = Join-Path $VenvPath 'Scripts\python.exe'
    $healthy = $false
    if (Test-Path $venvPython) {
        & $venvPython -c "import cv2,kornia,numpy,psycopg,redis,rich,torch; assert torch.__version__ == '$TorchVersion'" 2>$null
        $healthy = ($LASTEXITCODE -eq 0)
    }
    if ($healthy) {
        Write-Host "Existing Norm venv is healthy; keeping it."
        return
    }
    if (Test-Path $VenvPath) {
        Write-Host "Existing Norm venv is incomplete/mismatched; recreating it..."
        Remove-Item -LiteralPath $VenvPath -Recurse -Force
    } else {
        Write-Host "Creating Norm venv..."
    }
    & $BasePython -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed.' }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    Write-Host "Installing CUDA Torch $TorchVersion..."
    & $venvPython -m pip install "--index-url=$TorchIndex" "torch==$TorchVersion"
    if ($LASTEXITCODE -ne 0) { throw 'Torch installation failed.' }
    Write-Host 'Installing pinned Norm dependencies...'
    & $venvPython -m pip install -r $RequirementsFile
    if ($LASTEXITCODE -ne 0) { throw 'Norm dependency installation failed.' }
    & $venvPython -c "import cv2,kornia,numpy,psycopg,redis,rich,torch; print('venv ok', torch.__version__, 'cuda', torch.cuda.is_available())"
    if ($LASTEXITCODE -ne 0) { throw 'Recreated Norm venv failed import validation.' }
}

try {
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    Write-Host 'Extracting backup to temporary restore area...'
    Expand-Archive -LiteralPath $zip -DestinationPath $temp -Force
    $manifestPath = Join-Path $temp 'backup-manifest.json'
    if (-not (Test-Path $manifestPath)) { throw 'backup-manifest.json is missing from the ZIP.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $runtimeRoot = [string]$manifest.paths.runtime_root
    $workspaceRoot = [string]$manifest.paths.workspace_root
    $schema = [string]$manifest.postgres.schema
    $conninfo = [string]$manifest.postgres.conninfo
    $dumpPath = Join-Path $temp ([string]$manifest.postgres.dump)
    $pythonVersion = [string]$manifest.environment.python_version
    $majorMinor = (($pythonVersion -split '\.')[0..1] -join '.')
    $venvPath = [string]$manifest.environment.venv_path
    $requirementsFile = [string]$manifest.environment.requirements_file
    $torchVersion = [string]$manifest.environment.torch_version
    $torchIndex = [string]$manifest.environment.torch_index_url

    Write-Host "Backup:      $zip"
    Write-Host "Runtime:     $runtimeRoot"
    Write-Host "Workspace:   $workspaceRoot"
    Write-Host "PostgreSQL:  schema $schema"
    Write-Host "Python:      $pythonVersion (venv rebuilt only if needed)"
    if (Get-Process norm -ErrorAction SilentlyContinue) { throw 'norm.exe is running. Shut Norm down before restoring this backup.' }
    $confirm = Read-Host 'Type RESTORE to overlay these locations, rebuild environment if needed, and restore PostgreSQL'
    if ($confirm -cne 'RESTORE') { Write-Host 'Restore cancelled.'; exit 2 }

    $runtimeSource = Join-Path $temp 'runtime'
    $workspaceSource = Join-Path $temp 'workspace'
    if (-not (Test-Path $runtimeSource)) { throw 'runtime/ is missing from the backup.' }
    if (-not (Test-Path $workspaceSource)) { throw 'workspace/ is missing from the backup.' }
    Write-Host 'Restoring runtime files...'
    Invoke-RobocopyOverlay $runtimeSource $runtimeRoot
    Write-Host 'Restoring workspace files...'
    Invoke-RobocopyOverlay $workspaceSource $workspaceRoot
    if (-not (Test-Path $requirementsFile)) { throw "Requirements file is missing after runtime restore: $requirementsFile" }
    $basePython = Find-Python $majorMinor
    if (-not $basePython) { $basePython = Install-Python $pythonVersion $majorMinor }
    Ensure-NormVenv $basePython $venvPath $requirementsFile $torchVersion $torchIndex
    $pgRestore = [string]$manifest.postgres.pg_restore
    if (-not (Test-Path $pgRestore)) {
        $candidate = Get-ChildItem 'C:\Program Files\PostgreSQL' -Recurse -Filter pg_restore.exe -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
        if (-not $candidate) { throw 'pg_restore.exe was not found.' }
        $pgRestore = $candidate
    }
    if (-not (Test-Path $dumpPath)) { throw "PostgreSQL dump is missing: $dumpPath" }
    Write-Host 'Restoring PostgreSQL norm_runtime data...'
    & $pgRestore --clean --if-exists --no-owner "--dbname=$conninfo" $dumpPath
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed with exit code $LASTEXITCODE" }
    Write-Host 'Norm backup restore completed. Runtime environment is ready.'
}
finally {
    if (Test-Path $temp) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
