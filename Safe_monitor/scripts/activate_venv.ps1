#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Ativa o ambiente virtual Python
.DESCRIPTION
    Este script apenas ativa o venv, sem executar o monitor
.EXAMPLE
    .\activate_venv.ps1
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"

# Verifica se o venv existe
if (-not (Test-Path $VenvActivate)) {
    Write-Host "[✗] Ambiente virtual não encontrado!" -ForegroundColor Red
    Write-Host "[ℹ️]  Execute .\scripts\setup_venv.ps1 primeiro" -ForegroundColor Cyan
    exit 1
}

Write-Host "[✓] Ativando ambiente virtual..." -ForegroundColor Green

# Ativa o venv
& $VenvActivate

Write-Host "`n[✓] Ambiente virtual ativado!" -ForegroundColor Green
Write-Host "[ℹ️]  Para rodar o monitor: python src\main.py" -ForegroundColor Cyan
Write-Host "[ℹ️]  Ou execute: .\scripts\run_monitor.ps1`n" -ForegroundColor Cyan
