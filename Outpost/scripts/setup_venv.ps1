#!/usr/bin/env pwsh

param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot "venv"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"

if (-not (Test-Path $VenvPath)) {
    Write-Host "[INFO] Creating virtual environment..." -ForegroundColor Cyan
    python -m venv $VenvPath
}

$ActivatePath = Join-Path $VenvPath "Scripts\Activate.ps1"
& $ActivatePath
python -m pip install --upgrade pip
python -m pip install -r $RequirementsPath

Write-Host "`n[OK] Setup complete. Run .\scripts\run_outpost.ps1" -ForegroundColor Green
