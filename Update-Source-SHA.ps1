param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$ZipPath
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $ZipPath).Path
if ([IO.Path]::GetExtension($resolved) -ne ".zip") {
    throw "Expected a .zip file: $resolved"
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolved).Hash.ToLowerInvariant()
$name = [IO.Path]::GetFileName($resolved)
$out = "$resolved.sha256"
[IO.File]::WriteAllText($out, "$hash  $name`n", [Text.UTF8Encoding]::new($false))

Write-Host "SHA-256: $hash"
Write-Host "Wrote: $out"
