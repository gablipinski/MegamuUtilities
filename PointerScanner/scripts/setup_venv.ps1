#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Creates and initializes a Python virtual environment for PointerScanner.
.EXAMPLE
    .\scripts\setup_venv.ps1
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot "venv"
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$PythonExe = "python"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"

if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python was not found in PATH." -ForegroundColor Red
    Write-Host "[INFO] Install Python 3.10+ and try again." -ForegroundColor Cyan
    exit 1
}

if (-not (Test-Path $VenvPath)) {
    Write-Host "[INFO] Creating virtual environment..." -ForegroundColor Cyan
    & $PythonExe -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host "[WARN] Existing virtual environment found. Reusing it." -ForegroundColor Yellow
}

Write-Host "[INFO] Activating virtual environment..." -ForegroundColor Cyan
& $VenvActivate

Write-Host "[INFO] Upgrading pip..." -ForegroundColor Cyan
python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to upgrade pip." -ForegroundColor Red
    exit 1
}

if (Test-Path $RequirementsPath) {
    Write-Host "[INFO] Installing requirements..." -ForegroundColor Cyan
    python -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install requirements." -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n[OK] PointerScanner environment is ready." -ForegroundColor Green
Write-Host "[INFO] Run .\scripts\run_pointer_scanner.ps1" -ForegroundColor Cyan
