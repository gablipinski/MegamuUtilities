#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Activates the Python virtual environment
.DESCRIPTION
    This script only activates the venv, without running the monitor
.EXAMPLE
    .\activate_venv.ps1
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"

# Check if the venv exists
if (-not (Test-Path $VenvActivate)) {
    Write-Host "[ERROR] Virtual environment was not found." -ForegroundColor Red
    Write-Host "[INFO] Run .\scripts\setup_venv.ps1 first." -ForegroundColor Cyan
    exit 1
}

Write-Host "[INFO] Activating virtual environment..." -ForegroundColor Green

# Activate venv
& $VenvActivate

Write-Host "`n[OK] Virtual environment activated." -ForegroundColor Green
Write-Host "[INFO] To run the monitor: python src\main.py" -ForegroundColor Cyan
Write-Host "[INFO] Or run: .\scripts\run_watchtower.ps1`n" -ForegroundColor Cyan
