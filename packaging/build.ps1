<#
.SYNOPSIS
    Builds the packaged ED Colony Scout Windows executable via PyInstaller.

.DESCRIPTION
    Wraps `pyinstaller packaging/ed_colony_scout.spec --clean` so the build
    command is a single, repeatable step. Requires requirements-build.txt's
    dependencies (pyinstaller, pillow) installed in the active Python
    environment -- these are build-only, not part of requirements.txt.

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
    Write-Host "Build complete: dist/ED Colony Scout/ED Colony Scout.exe" -ForegroundColor Green
}
finally {
    Pop-Location
}
