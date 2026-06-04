# Script para executar o bot
# Uso:
#   .\scripts\run_bot.ps1
#   .\scripts\run_bot.ps1 -Log
#   .\scripts\run_bot.ps1 -LogOnly
#   .\scripts\run_bot.ps1 -LogOnly -- --help

param(
    [switch]$Log,
    [switch]$LogOnly,
    [switch]$Gui
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $ProjectRoot

$CandidatePythons = @()
if ($env:VIRTUAL_ENV) {
    $CandidatePythons += (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")
}
$CandidatePythons += (Join-Path $ProjectRoot "venv\Scripts\python.exe")
$CandidatePythons += (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
$CandidatePythons += (Join-Path $WorkspaceRoot ".venv\Scripts\python.exe")

function Test-PythonHasModules {
    param(
        [string]$PythonPath,
        [string[]]$Modules
    )

    if (-not (Test-Path $PythonPath)) {
        return $false
    }

    $moduleCsv = ($Modules -join ',')
    $probeCode = @"
import importlib.util
mods = '$moduleCsv'.split(',') if '$moduleCsv' else []
missing = [m for m in mods if importlib.util.find_spec(m) is None]
raise SystemExit(1 if missing else 0)
"@

    & $PythonPath -c $probeCode *> $null
    return ($LASTEXITCODE -eq 0)
}

$RequiredModules = @('twitchio')
if ($Gui) {
    $RequiredModules += 'textual'
}

$PythonExe = $null
foreach ($candidate in $CandidatePythons) {
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        continue
    }
    if (Test-PythonHasModules -PythonPath $candidate -Modules $RequiredModules) {
        $PythonExe = $candidate
        break
    }
}

# Garante que o venv local do projeto exista.
if (-not $PythonExe) {
    Write-Host "Nenhum Python valido encontrado com modulos: $($RequiredModules -join ', ')." -ForegroundColor Red
    foreach ($candidate in $CandidatePythons) {
        Write-Host " - $candidate" -ForegroundColor DarkGray
    }
    Write-Host "Ative o ambiente correto ou instale dependencias nesse ambiente." -ForegroundColor Yellow
    Write-Host "Exemplo: <python> -m pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host "Iniciando Bot Twitch..." -ForegroundColor Cyan
Write-Host "Python: $PythonExe" -ForegroundColor DarkGray
Write-Host ""

$MainArgs = @()
if ($Log) {
    $MainArgs += "--log"
    Write-Host "Modo selecionado: full + logging" -ForegroundColor Yellow
}
if ($Gui) {
    $MainArgs += "--gui"
    Write-Host "Modo selecionado: TUI monitor" -ForegroundColor Cyan
}
if ($LogOnly) {
    $MainArgs += "--log-only"
    Write-Host "Modo selecionado: logging-only" -ForegroundColor Yellow
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
