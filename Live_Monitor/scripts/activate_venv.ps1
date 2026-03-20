# Script para ativar o venv manualmente
# Uso: .\scripts\activate_venv.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "[⏳] Ativando venv..." -ForegroundColor Cyan
& "$ProjectRoot\venv\Scripts\Activate.ps1"
Write-Host "[✓] Venv ativado!" -ForegroundColor Green
Write-Host ""
Write-Host "Você agora pode rodar comandos Python diretamente." -ForegroundColor Green
