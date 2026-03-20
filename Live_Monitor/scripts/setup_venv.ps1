# Script para criar venv e instalar dependências
# Uso: .\scripts\setup_venv.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "[⏳] Criando ambiente virtual..." -ForegroundColor Cyan
python -m venv "$ProjectRoot\venv"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[✗] Erro ao criar venv" -ForegroundColor Red
    exit 1
}

Write-Host "[✓] Venv criado com sucesso" -ForegroundColor Green

# Ativa o venv
& "$ProjectRoot\venv\Scripts\Activate.ps1"

Write-Host "[⏳] Instalando dependências..." -ForegroundColor Cyan
pip install -r "$ProjectRoot\requirements.txt" --upgrade

if ($LASTEXITCODE -ne 0) {
    Write-Host "[✗] Erro ao instalar dependências" -ForegroundColor Red
    exit 1
}

Write-Host "[✓] Setup concluído com sucesso!" -ForegroundColor Green
Write-Host ""
Write-Host "Próximas etapas:" -ForegroundColor Cyan
Write-Host "1. Configure os canais da Twitch em: $ProjectRoot\configs\config.json"
Write-Host "2. Execute o bot com: .\scripts\run_bot.ps1"
