#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Ativa o ambiente virtual e inicia o Safe Monitor
.DESCRIPTION
    Este script ativa o venv Python e executa o monitor de tela
.EXAMPLE
    .\run_monitor.ps1
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$ConfigPath = Join-Path $ProjectRoot "configs\config.json"
$MainPath = Join-Path $ProjectRoot "src\main.py"

# Verifica se o venv existe
if (-not (Test-Path $VenvActivate)) {
    Write-Host "[✗] Ambiente virtual não encontrado!" -ForegroundColor Red
    Write-Host "[ℹ️]  Execute .\scripts\setup_venv.ps1 primeiro" -ForegroundColor Cyan
    exit 1
}

# Verifica se config.json existe
if (-not (Test-Path $ConfigPath)) {
    Write-Host "[✗] Arquivo configs\config.json não encontrado!" -ForegroundColor Red
    Write-Host "[ℹ️]  Copie/edite o arquivo config.json e configure as janelas de jogo" -ForegroundColor Cyan
    exit 1
}

Write-Host "`n📺 Iniciando Safe Monitor...`n" -ForegroundColor Green

# Ativa o venv
& $VenvActivate

# Executa o monitor
python $MainPath

# Se o monitor foi encerrado, exibe mensagem
Write-Host "`n[⏹️]  Monitor encerrado" -ForegroundColor Yellow
