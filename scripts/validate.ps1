# Pre-tag validation: build the extension zip and run Blender's manifest validator.
# Requires `blender` on PATH. On Windows, the Blender installer usually does NOT add
# it to PATH — add "C:\Program Files\Blender Foundation\Blender 4.2" (or similar) to
# your PATH, or edit $BlenderExe below.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "Building extension zip..."
python build.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Zip = Get-ChildItem "$RepoRoot\build\ai_model_optimizer-*.zip" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $Zip) {
    Write-Error "No ai_model_optimizer-*.zip found in build/"
    exit 1
}

Write-Host "Validating $($Zip.Name) with Blender..."

$BlenderExe = Get-Command blender -ErrorAction SilentlyContinue
if (-not $BlenderExe) {
    Write-Error "blender not found on PATH. Install Blender 4.2+ and add it to PATH, or edit this script."
    exit 2
}

& blender --command extension validate $Zip.FullName
if ($LASTEXITCODE -ne 0) {
    Write-Error "Blender validation failed."
    exit $LASTEXITCODE
}

Write-Host "OK: $($Zip.Name) validated."
