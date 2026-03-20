#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Cria e ativa um ambiente virtual Python para Safe Monitor
.DESCRIPTION
    Este script cria um novo ambiente virtual Python na pasta 'venv'
    e ativa automaticamente ao final
.EXAMPLE
    .\setup_venv.ps1
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot "venv"
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"

Write-Host "`n🐍 Criando ambiente virtual Python para Safe Monitor..." -ForegroundColor Cyan

# Verifica se já existe um venv
if (Test-Path $VenvPath) {
    Write-Host "[⚠️]  Ambiente virtual já existe." -ForegroundColor Yellow
    Write-Host "[ℹ️]  Execute .\scripts\activate_venv.ps1 para ativar." -ForegroundColor Cyan
    exit 0
}

# Cria o venv
Write-Host "[⏳] Criando venv..." -ForegroundColor Cyan
python -m venv $VenvPath

if ($LASTEXITCODE -eq 0) {
    Write-Host "[✓] Ambiente virtual criado com sucesso!" -ForegroundColor Green
    
    # Ativa o venv
    Write-Host "[⏳] Ativando ambiente virtual..." -ForegroundColor Cyan
    & $VenvActivate
    
    # Instala as dependências
    Write-Host "`n[⏳] Instalando dependências..." -ForegroundColor Cyan
    pip install -r $RequirementsPath
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n[✓] Setup concluído com sucesso!" -ForegroundColor Green
        Write-Host "[ℹ️]  Para ativar o venv novamente, execute: .\scripts\activate_venv.ps1" -ForegroundColor Cyan
        Write-Host "[ℹ️]  Para rodar o monitor, execute: .\scripts\run_monitor.ps1`n" -ForegroundColor Cyan
    } else {
        Write-Host "`n[✗] Erro ao instalar dependências!" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[✗] Erro ao criar ambiente virtual!" -ForegroundColor Red
    exit 1
}
