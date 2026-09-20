$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root 'app\norm.exe'
$settingsPath = Join-Path $root 'config\settings.ini'
if (-not (Test-Path $settingsPath)) { throw "Norm settings not found: $settingsPath" }

function Get-IniSection([string]$Path, [string]$Section) {
    $values = @{}
    $current = ''
    foreach ($raw in Get-Content $Path) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith(';') -or $line.StartsWith('#')) { continue }
        if ($line -match '^\[(.+)\]$') { $current = $Matches[1].Trim(); continue }
        if ($current -eq $Section -and $line -match '^([^=]+)=(.*)$') {
            $values[$Matches[1].Trim()] = $Matches[2].Trim()
        }
    }
    return $values
}

$ports = Get-IniSection $settingsPath 'ports'
$normPort = [int]$ports.norm_http
$activityPort = [int]$ports.activity
$ollamaPort = [int]$ports.ollama
$health = "http://127.0.0.1:$normPort/health"
$events = "http://127.0.0.1:$activityPort/events"
Write-Host 'Norm local console' -ForegroundColor Cyan
Write-Host "Ports:    Ollama=$ollamaPort Norm=$normPort Activity=$activityPort"
Write-Host "Health:   $health"
Write-Host "Activity: $events"
Write-Host ''

$status = $null
try { $status = Invoke-RestMethod $health -TimeoutSec 2 } catch {
    Write-Host 'Norm service is offline; starting it...' -ForegroundColor Yellow
    Start-Process -FilePath $exe -WindowStyle Minimized
}
for ($i = 0; $i -lt 30 -and -not $status; $i++) {
    try { $status = Invoke-RestMethod $health -TimeoutSec 2 } catch { Start-Sleep -Seconds 1 }
}
if (-not $status) { Write-Host 'Norm did not become healthy.' -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }
Write-Host "Norm healthy: $($status | ConvertTo-Json -Compress)" -ForegroundColor Green
Write-Host 'Opening live Norm console...' -ForegroundColor Cyan
& $exe --console
