#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Activates the virtual environment and starts Watchtower
.DESCRIPTION
    This script activates the Python venv and runs the monitor UI
.EXAMPLE
    .\run_watchtower.ps1
#>

param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$ConfigPath = Join-Path $ProjectRoot "configs\config.py"
$MainPath = Join-Path $ProjectRoot "src\main.py"

# Check if the venv exists
if (-not (Test-Path $VenvActivate)) {
    Write-Host "[ERROR] Virtual environment was not found." -ForegroundColor Red
    Write-Host "[INFO] Run .\scripts\setup_venv.ps1 first." -ForegroundColor Cyan
    exit 1
}

# Check if config.py exists
if (-not (Test-Path $ConfigPath)) {
    Write-Host "[ERROR] File configs\config.py was not found." -ForegroundColor Red
    Write-Host "[INFO] Copy/edit config.py and configure game windows." -ForegroundColor Cyan
    exit 1
}

Write-Host "`n[INFO] Starting Watchtower...`n" -ForegroundColor Green

# Activate venv
& $VenvActivate

Write-Host "[OK] Startup mode: UI" -ForegroundColor Green
python $MainPath

# Show a message when monitoring exits
Write-Host "`n[INFO] Monitor exited" -ForegroundColor Yellow
