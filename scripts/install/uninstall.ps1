# ggshield uninstaller for Windows.
#
# By default removes ONLY the install this script created (per-user ZIP, or the
# MSI when installed with -Method msi). It does not touch ggshield installed
# another way (Chocolatey, a hand-run MSI, pipx, uv, pip). Pass -Purge to also
# remove ggshield's config, cache and data.
#
#   irm https://raw.githubusercontent.com/GitGuardian/ggshield/main/scripts/install/uninstall.ps1 | iex
#
# Compatible with Windows PowerShell 5.1 (no pwsh required).

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'

$ZipDir = Join-Path $env:LOCALAPPDATA 'Programs\ggshield'
$StateDir = Join-Path $env:LOCALAPPDATA 'ggshield-install'
$StateFile = Join-Path $StateDir 'state.json'

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Blue }
function Warn($msg) { Write-Host "warning: $msg" -ForegroundColor Yellow }
function Die($msg) { throw "error: $msg" }

function Confirm-Step($prompt) {
    if ($Yes) { return $true }
    $reply = Read-Host "$prompt [y/N]"
    return $reply -match '^[yY]'
}

# Run a native command, discarding output. With $ErrorActionPreference=Stop,
# redirecting native stderr would otherwise throw (PowerShell 5.1 gotcha).
function Invoke-Native {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $args[0] @($args | Select-Object -Skip 1) 2>&1 | Out-Null }
    finally { $ErrorActionPreference = $eap }
    return $LASTEXITCODE
}

# Never delete a misconfigured GGSHIELD location (empty, a drive root, the
# user profile or LocalAppData root).
function Assert-SafeZipDir {
    $p = $ZipDir.TrimEnd('\', '/')
    $unsafe = @($env:USERPROFILE.TrimEnd('\'), $env:LOCALAPPDATA.TrimEnd('\'))
    if ([string]::IsNullOrWhiteSpace($p) -or $p -match '^[A-Za-z]:\\?$' -or $unsafe -contains $p) {
        Die "refusing to remove unsafe path '$ZipDir'"
    }
}

function Get-MsiProduct {
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    Get-ItemProperty $keys -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like 'ggshield*' } |
        Select-Object -First 1
}

# Remove only the install this script created, per the recorded method.
function Remove-Standalone {
    $method = 'zip'
    if (Test-Path $StateFile) {
        try { $method = (Get-Content $StateFile -Raw | ConvertFrom-Json).method } catch {}
    }

    if ($method -eq 'msi') {
        $product = Get-MsiProduct
        if (-not $product) { Warn 'recorded MSI install not found; nothing to remove'; return }
        if (-not (Confirm-Step "Remove the ggshield MSI ($($product.DisplayVersion))?")) { return }
        Say 'Removing the ggshield MSI'
        $proc = Start-Process msiexec -ArgumentList '/x', $product.PSChildName, '/qn', '/norestart' -Wait -PassThru
        if ($proc.ExitCode -ne 0) { Warn "msiexec failed with exit code $($proc.ExitCode)" }
    }
    else {
        if (-not (Test-Path $ZipDir)) {
            Warn "no script-managed install found at $ZipDir"
            Warn 'ggshield installed another way (choco, a hand-run MSI, pipx, uv, pip) is left untouched'
            return
        }
        if (-not (Confirm-Step "Remove the standalone ggshield at $ZipDir?")) { return }
        Assert-SafeZipDir
        Say "Removing $ZipDir"
        Remove-Item $ZipDir -Recurse -Force
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $cleaned = ($userPath -split ';' | Where-Object { $_ -and $_ -notlike "$ZipDir*" }) -join ';'
        if ($cleaned -ne $userPath) {
            Say 'Removing ggshield from your user PATH'
            [Environment]::SetEnvironmentVariable('Path', $cleaned, 'User')
        }
    }
    if (Test-Path $StateDir) { Remove-Item $StateDir -Recurse -Force }
}

function Remove-UserData {
    # platformdirs(appname="ggshield", appauthor="GitGuardian") on Windows
    $paths = @(
        (Join-Path $env:USERPROFILE '.gitguardian.yaml'),
        (Join-Path $env:LOCALAPPDATA 'GitGuardian\ggshield')
    )
    $found = $paths | Where-Object { Test-Path $_ }
    if (-not $found) { Say 'No ggshield config/cache/data found'; return }
    if (-not (Confirm-Step 'Remove ggshield configuration, cache and data (including plugins)?')) { return }
    foreach ($p in $found) { Say "Removing $p"; Remove-Item $p -Recurse -Force }
}

Remove-Standalone
if ($Purge) { Remove-UserData }
Say 'Done.'
