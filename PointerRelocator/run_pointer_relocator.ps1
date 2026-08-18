#!/usr/bin/env pwsh

param()

$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot 'venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    Write-Host '[ERROR] Virtual environment was not found.' -ForegroundColor Red
    Write-Host '[INFO] Create it with: py -m venv venv' -ForegroundColor Cyan
    Write-Host '[INFO] Then install dependencies with: .\venv\Scripts\python.exe -m pip install -r requirements.txt' -ForegroundColor Cyan
    exit 1
}

& $Python (Join-Path $ProjectRoot 'pointer_relocator.py')