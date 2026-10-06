[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Settings = Join-Path $Root 'config\settings.ini'
$Python = Join-Path $Root '.venv\Scripts\python.exe'

function Read-NormMenuInput {
    param([string]$Label)
    Write-Host "$Label " -NoNewline
    $Text = ''
    $PreviousControlC = [Console]::TreatControlCAsInput
    try {
        [Console]::TreatControlCAsInput = $true
        while ($true) {
            $Key = [Console]::ReadKey($true)
            if ($Key.Key -eq [ConsoleKey]::Escape -or
                ($Key.Key -eq [ConsoleKey]::C -and ($Key.Modifiers -band [ConsoleModifiers]::Control))) {
                Write-Host ''
                return $null
            }
            if ($Key.Key -eq [ConsoleKey]::Enter) {
                Write-Host ''
                return $Text
            }
            if ($Key.Key -eq [ConsoleKey]::Backspace) {
                if ($Text.Length -gt 0) {
                    $Text = $Text.Substring(0, $Text.Length - 1)
                    Write-Host "`b `b" -NoNewline
                }
            } elseif (-not [char]::IsControl($Key.KeyChar)) {
                $Text += $Key.KeyChar
                Write-Host $Key.KeyChar -NoNewline
            }
        }
    } finally {
        [Console]::TreatControlCAsInput = $PreviousControlC
    }
}

function Show-NormViewer {
    param([string]$Name, [string]$Title)
    $ViewerPath = Join-Path $Root "tools\$Name"
    if (-not (Test-Path -LiteralPath $ViewerPath -PathType Leaf)) {
        Write-Host "Viewer missing: $ViewerPath" -ForegroundColor Red
        return
    }
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
    Write-Host 'Esc, Ctrl+C, or ENTER returns to the menu. Norm keeps running.'
    $ViewerProcess = $null
    try {
        $ViewerProcess = Start-Process -FilePath $Python -ArgumentList ('-u "{0}"' -f $ViewerPath) -WorkingDirectory $Root -NoNewWindow -PassThru
        $null = Read-NormMenuInput 'Return to menu:'
    } finally {
        if ($null -ne $ViewerProcess) {
            try {
                if (-not $ViewerProcess.HasExited) {
                    # Only close the viewer this function started, never Norm.
                    $ViewerProcess.Kill()
                    $ViewerProcess.WaitForExit()
                }
            } finally { $ViewerProcess.Dispose() }
        }
    }
    Write-Host 'Returned to menu. No stop command was sent to Norm.'
}

function Test-Http {
    param([string]$Url)
    try {
        $null = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 3 -ErrorAction Stop
        return $true
    } catch { return $false }
}

function Invoke-NormClient {
    param([string]$Name)
    $ClientPath = Join-Path $Root "tools\$Name"
    if (-not (Test-Path -LiteralPath $ClientPath -PathType Leaf)) {
        Write-Host "Norm client missing: $ClientPath" -ForegroundColor Red
        return
    }
    & $Python $ClientPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Norm client exited with code $LASTEXITCODE" -ForegroundColor Red
    }
}

try {
    foreach ($RequiredPath in @($Settings, $Python)) {
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "Required Norm file missing: $RequiredPath"
        }
    }
    # Use exactly the same port keys and host resolution as the runtime.
    $ConfigCode = @"
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'core'))
from norm_runtime.settings import load_network_settings, load_ports, resolve_network_host
network = load_network_settings(root)
ports = load_ports(root)
print(json.dumps({'norm_host': resolve_network_host(network, 'norm_host'), 'activity_host': resolve_network_host(network, 'activity_host'), 'ollama_host': resolve_network_host(network, 'ollama_host'), 'norm_port': ports['norm_http'], 'activity_port': ports['activity'], 'ollama_port': ports['ollama']}))
"@
    $ConfigJson = & $Python -B -c $ConfigCode $Root
    if ($LASTEXITCODE -ne 0) { throw 'Norm network settings could not be loaded.' }
    $Config = ($ConfigJson -join "`n") | ConvertFrom-Json
    $NormBase = [UriBuilder]::new('http', [string]$Config.norm_host, [int]$Config.norm_port)
    $ActivityBase = [UriBuilder]::new('http', [string]$Config.activity_host, [int]$Config.activity_port)
    $OllamaBase = [UriBuilder]::new('http', [string]$Config.ollama_host, [int]$Config.ollama_port)
    $OllamaStatusUrl = [Uri]::new($OllamaBase.Uri, '/api/tags').AbsoluteUri
    $NormHealthUrl = [Uri]::new($NormBase.Uri, '/health').AbsoluteUri
    $ActivityHealthUrl = [Uri]::new($ActivityBase.Uri, '/health').AbsoluteUri
    $ActivityStatusUrl = [Uri]::new($ActivityBase.Uri, '/status/busy').AbsoluteUri
    $NormHttpOK = Test-Http $NormHealthUrl
    $ActivityOK = Test-Http $ActivityHealthUrl
    $OllamaOK = Test-Http $OllamaStatusUrl
    $RuntimeStatusOK = Test-Http $ActivityStatusUrl

    Write-Host "`n---------------- Norm Remote ----------------" -ForegroundColor Cyan
    foreach ($Service in @(
        @{ Name = 'Norm HTTP'; Online = $NormHttpOK; Url = $NormHealthUrl },
        @{ Name = 'Activity HTTP'; Online = $ActivityOK; Url = $ActivityHealthUrl },
        @{ Name = 'Runtime status'; Online = $RuntimeStatusOK; Url = $ActivityStatusUrl },
        @{ Name = 'Ollama API'; Online = $OllamaOK; Url = $OllamaStatusUrl }
    )) {
        $State = if ($Service.Online) { 'REACHABLE' } else { 'UNREACHABLE' }
        $Color = if ($Service.Online) { 'Green' } else { 'Red' }
        Write-Host "$($Service.Name) $State  $($Service.Url)" -ForegroundColor $Color
    }
    Write-Host 'HTTP reachability does not verify model inference, databases, or plugins.'
    if ($CheckOnly) {
        [pscustomobject]@{ NormOnline = $NormHttpOK; ActivityOnline = $ActivityOK; OllamaOnline = $OllamaOK; RuntimeStatusAvailable = $RuntimeStatusOK; NormHealthUrl = $NormHealthUrl; ActivityHealthUrl = $ActivityHealthUrl }
        return
    }
    if (-not $NormHttpOK -or -not $ActivityOK) {
        $StartCandidates = @('Run-Norm.bat', 'Run Norm.bat', 'Start-Norm.bat', 'Start Norm.bat')
        $StartBat = $StartCandidates | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $StartBat) { throw "No Norm startup BAT found in $Root" }
        Write-Host 'A service is unreachable. This can also mean a Tailscale or network problem.' -ForegroundColor Yellow
        $Answer = Read-NormMenuInput 'Run the Norm service startup health checks? [Y/N, Esc=back]'
        if ($Answer -match '^(?i:y|yes)$') {
            Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /s /c ""{0}" --service-only"' -f $StartBat) -WorkingDirectory $Root -WindowStyle Hidden
            Write-Host 'Startup requested. Run norm again after startup completes.' -ForegroundColor Green
        }
        return
    }
    while ($true) {
        $Choice = Read-NormMenuInput 'P=prompt R=replies L=live S=status Q=quit Esc=back'
        if ($null -eq $Choice) { return }
        $Choice = $Choice.Trim().ToLowerInvariant()
        switch ($Choice) {
            'p' {
                Write-Host 'Esc or /exit returns to this menu. Use /stop-all to shut down Norm.'
                Invoke-NormClient 'norm_gui_prompt.py'
            }
            'r' { Show-NormViewer 'norm_gui_reply.py' 'Norm Replies' }
            'l' { Show-NormViewer 'norm_gui_stream.py' 'Norm Live Stream' }
            's' {
                Write-Host ('Norm HTTP reachable: {0}' -f (Test-Http $NormHealthUrl))
                Write-Host ('Activity HTTP reachable: {0}' -f (Test-Http $ActivityHealthUrl))
                Write-Host ('Ollama API reachable: {0}' -f (Test-Http $OllamaStatusUrl))
                try { Invoke-RestMethod -Uri $ActivityStatusUrl -TimeoutSec 3 | ConvertTo-Json -Depth 8 }
                catch { Write-Host "Norm status unavailable: $($_.Exception.Message)" -ForegroundColor Red }
            }
            'q' {
                Write-Host 'Leaving Norm remote menu. No stop command was sent to Norm.'
                return
            }
            default { Write-Host 'Choose P, R, L, S, or Q.' }
        }
    }
} catch {
    Write-Error "Norm remote: $($_.Exception.Message)"
}
