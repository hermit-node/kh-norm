param(
    [switch]$Push,
    [string]$Message = "Release Norm 0.53.11 / Unified Installer 1.6.5",
    [string]$Remote = "https://github.com/hermit-node/kh-norm.git"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot

function Run-Git {
    param([string[]]$GitArgs)

    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required and was not found in PATH."
}

if (-not (Test-Path ".git")) {
    Run-Git @("init")
}

Run-Git @("branch", "-M", "main")

# Local/private operator material may exist beside the installer, but must be ignored.
$privatePaths = @(
    "norm-imprint.local.json",
    ".env",
    "secrets",
    ".ssh"
)

foreach ($item in $privatePaths) {
    if (Test-Path $item) {
        & git check-ignore -q -- $item
        if ($LASTEXITCODE -ne 0) {
            throw "Private/local path exists but is not ignored by Git: $item"
        }
    }
}

# Add origin only when absent. Never silently replace a different existing remote.
$remoteNames = @(& git remote)
if ($LASTEXITCODE -ne 0) {
    throw "Could not list Git remotes."
}

if ($remoteNames -contains "origin") {
    $origin = (& git remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read existing origin remote."
    }
    if ($origin -ne $Remote) {
        throw "Existing origin is '$origin'; expected '$Remote'. Refusing to replace it automatically."
    }
} else {
    Run-Git @("remote", "add", "origin", $Remote)
    Write-Host "Added origin: $Remote"
}

$publicFiles = @(
    ".gitignore",
    ".gitattributes",
    "README.md",
    "SECURITY.md",
    "RELEASE.md",
    "AUDIT.md",
    "PUBLISH.md",
    "Norm-Installer.py",
    "installer_environment.py",
    "Run-Norm-Installer.bat",
    "Build-Norm-Installer-EXE.bat",
    "Norm-0.53.11-portable-source.zip",
    "Norm-0.53.11-portable-source.zip.sha256",
    "norm-imprint.example.json",
    "Publish-To-GitHub.ps1",
    "tests",
    "src",
    "tools",
    "VALIDATION.txt",
    "SHA256SUMS.txt"
)

# Intentionally add one path at a time. Windows PowerShell 5.1 can otherwise
# collapse an array passed through a wrapper into one giant pathspec.
foreach ($item in $publicFiles) {
    if (-not (Test-Path $item)) {
        throw "Required public release path is missing: $item"
    }
    Run-Git @("add", "--", $item)
}

$staged = @(& git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect staged files."
}

if ($staged.Count -eq 0) {
    Write-Host "Nothing new to commit."
} else {
    $forbiddenNamePattern = 'norm-imprint\.local\.json|(^|/)\.env($|/)|(^|/)secrets/|(^|/)\.ssh/|\.key$|\.pem$|\.pfx$|\.p12$'
    if (($staged -join "`n") -match $forbiddenNamePattern) {
        throw "Refusing to publish: staged private/secret material detected."
    }

    # Inspect staged textual content for common credential signatures.
    $diff = & git diff --cached --text
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect staged diff."
    }

    $forbiddenPatterns = @(
        "tskey-[A-Za-z0-9_-]{10,}",
        "ghp_[A-Za-z0-9]{20,}",
        "github_pat_[A-Za-z0-9_]{20,}",
        "AKIA[0-9A-Z]{16}",
        "-----BEGIN [A-Z ]*PRIVATE KEY-----"
    )

    foreach ($pattern in $forbiddenPatterns) {
        if ($diff | Select-String -Pattern $pattern -Quiet) {
            throw "Refusing to publish: staged content matched credential pattern: $pattern"
        }
    }

    Run-Git @("diff", "--cached", "--check")

    Write-Host ""
    Write-Host "Staged public files:"
    & git status --short

    Run-Git @("commit", "-m", $Message)
}

if ($Push) {
    Run-Git @("push", "-u", "origin", "main")
    Write-Host ""
    Write-Host "Published main to $Remote"
} else {
    Write-Host ""
    Write-Host "Repository prepared locally. Publish with:"
    Write-Host "  .\Publish-To-GitHub.ps1 -Push"
}

Write-Host ""
Write-Host "The local norm-imprint.local.json is intentionally ignored and is not uploaded."
Write-Host "GitHub CLI is not required."
Write-Host ""
Write-Host "After main is pushed, create the tag with normal Git:"
Write-Host '  git tag -a v0.53.11 -m "Norm 0.53.11 / Unified Installer 1.6.5"'
Write-Host "  git push origin v0.53.11"
Write-Host ""
Write-Host "If gh.exe is not installed, create the GitHub Release from the repository web UI."
