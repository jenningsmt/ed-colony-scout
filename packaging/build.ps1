<#
.SYNOPSIS
    Builds the packaged ED Colony Scout Windows executable and installer.

.DESCRIPTION
    Two steps: `pyinstaller packaging/ed_colony_scout.spec --clean` produces
    the onedir build, then `iscc packaging/installer.iss` wraps it into a
    per-user Inno Setup installer. Requires requirements-build.txt's
    dependencies (pyinstaller, pillow) installed in the active Python
    environment (build-only, not part of requirements.txt), and Inno Setup
    (https://jrsoftware.org/isinfo.php) installed with `iscc` on PATH.

.EXAMPLE
    powershell -File packaging/build.ps1
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    pyinstaller packaging/ed_colony_scout.spec --clean
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE"
    }
    Write-Host ""
    Write-Host "PyInstaller build complete: dist/ED Colony Scout/ED Colony Scout.exe" -ForegroundColor Green

    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        throw "iscc (Inno Setup) not found on PATH. Install it from " + `
              "https://jrsoftware.org/isinfo.php, then re-run this script."
    }

    iscc packaging/installer.iss
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup exited with code $LASTEXITCODE"
    }

    $installerPath = "dist-installer/ED-Colony-Scout-Setup-1.0.0.exe"
    $hash = Get-FileHash $installerPath -Algorithm SHA256
    Write-Host ""
    Write-Host "Installer built: $installerPath" -ForegroundColor Green
    Write-Host "SHA-256: $($hash.Hash)" -ForegroundColor Green
    Write-Host "(paste this checksum into the GitHub Release notes)"
}
finally {
    Pop-Location
}
