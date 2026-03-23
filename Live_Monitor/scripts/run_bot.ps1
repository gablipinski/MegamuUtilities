# Script para executar o bot
# Uso:
#   .\scripts\run_bot.ps1
#   .\scripts\run_bot.ps1 -LogOnly
#   .\scripts\run_bot.ps1 -LogOnly -- --help

param(
    [switch]$LogOnly
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"

# Garante que o venv local do projeto exista.
if (-not (Test-Path $PythonExe)) {
    Write-Host "[✗] Python do venv não encontrado em: $PythonExe" -ForegroundColor Red
    Write-Host "[i] Execute primeiro: .\scripts\setup_venv.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "[🤖] Iniciando Bot Twitch..." -ForegroundColor Cyan
Write-Host ""

$MainArgs = @()
if ($LogOnly) {
    $MainArgs += "--log-only"
    Write-Host "[ℹ] Modo selecionado: logging-only" -ForegroundColor Yellow
}

# Permite repassar argumentos adicionais ao main.py
if ($args.Count -gt 0) {
    $MainArgs += $args
}

Push-Location "$ProjectRoot\src"
& $PythonExe main.py @MainArgs
$exitCode = $LASTEXITCODE
Pop-Location

exit $exitCode
