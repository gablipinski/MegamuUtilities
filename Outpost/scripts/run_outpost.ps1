#!/usr/bin/env pwsh
<#!
.SYNOPSIS
    Activates the virtual environment and starts Outpost.
.DESCRIPTION
    This script activates the Python venv and runs the Outpost UI.
#>

param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$MainPath = Join-Path $ProjectRoot "src\main.py"

if (-not (Test-Path $VenvActivate)) {
    Write-Host "[ERROR] Virtual environment was not found." -ForegroundColor Red
    Write-Host "[INFO] Run .\scripts\setup_venv.ps1 first." -ForegroundColor Cyan
    exit 1
}

Write-Host "`n[INFO] Starting Outpost...`n" -ForegroundColor Green

& $VenvActivate
python $MainPath

Write-Host "`n[INFO] Outpost exited" -ForegroundColor Yellow
