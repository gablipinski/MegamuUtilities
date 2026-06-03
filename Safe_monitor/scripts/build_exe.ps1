#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Compila o Safe Monitor para um executavel Windows.

.DESCRIPTION
    Gera dist\SafeMonitor.exe usando Nuitka (compilacao para binario nativo).
    Isso aumenta bastante a protecao em relacao a distribuir .py, mas nao existe
    protecao absoluta contra engenharia reversa.

.EXAMPLE
    .\scripts\build_exe.ps1
#>

param(
    [switch]$Clean = $true
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$MainPath = Join-Path $ProjectRoot "src\main.py"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"

if (-not (Test-Path $VenvActivate)) {
    Write-Host "[✗] Ambiente virtual nao encontrado." -ForegroundColor Red
    Write-Host "[ℹ️ ] Execute .\scripts\setup_venv.ps1 primeiro." -ForegroundColor Cyan
    exit 1
}

if (-not (Test-Path $MainPath)) {
    Write-Host "[✗] Arquivo src\main.py nao encontrado." -ForegroundColor Red
    exit 1
}

Write-Host "`n🧱 Build do Safe Monitor (Nuitka)`n" -ForegroundColor Green

& $VenvActivate

if ($Clean) {
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "[1/3] Instalando dependencias de build..." -ForegroundColor Cyan
python -m pip install --upgrade pip | Out-Null
python -m pip install --upgrade nuitka ordered-set zstandard | Out-Null

Write-Host "[2/3] Compilando executavel..." -ForegroundColor Cyan
python -m nuitka `
    --onefile `
    --standalone `
    --assume-yes-for-downloads `
    --remove-output `
    --lto=yes `
    --python-flag=no_docstrings `
    --python-flag=-O `
    --enable-plugin=tk-inter `
    --windows-console-mode=disable `
    --include-package=pyautogui `
    --include-package=pyscreeze `
    --include-package=mouseinfo `
    --output-dir="$DistDir" `
    --output-filename="SafeMonitor.exe" `
    "$MainPath"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[✗] Falha na compilacao." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[3/3] Finalizado." -ForegroundColor Cyan
Write-Host "[✓] EXE gerado em: $DistDir\SafeMonitor.exe" -ForegroundColor Green
Write-Host "`n[ℹ️ ] Nota de seguranca:" -ForegroundColor Yellow
Write-Host "     Compilacao nativa dificulta bastante recuperar codigo-fonte," -ForegroundColor Yellow
Write-Host "     mas nao existe protecao 100% contra engenharia reversa." -ForegroundColor Yellow
