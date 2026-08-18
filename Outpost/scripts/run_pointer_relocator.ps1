#!/usr/bin/env pwsh

param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
$AppPath = Join-Path $ProjectRoot 'src\pointer_relocator.py'

if (-not (Test-Path $VenvPython)) {
    Write-Host '[ERROR] Virtual environment was not found.' -ForegroundColor Red
    Write-Host '[INFO] Run .\scripts\setup_venv.ps1 first.' -ForegroundColor Cyan
    exit 1
}

& $VenvPython $AppPath