# Script para executar o bot
# Uso: .\scripts\run_bot.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot

# Ativa o venv se não estiver ativado
if (-not $env:VIRTUAL_ENV) {
    Write-Host "[⏳] Ativando venv..." -ForegroundColor Cyan
    & "$ProjectRoot\venv\Scripts\Activate.ps1"
}

Write-Host "[🤖] Iniciando Bot Twitch..." -ForegroundColor Cyan
Write-Host ""

cd "$ProjectRoot\src"
python main.py
