#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Activates virtual environment and starts PointerScanner.
.EXAMPLE
    .\scripts\run_pointer_scanner.ps1
#>

param(
    [switch]$Cli,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$AppPath = Join-Path $ProjectRoot "src\pointer_finder.py"

if (-not (Test-Path $VenvActivate)) {
    Write-Host "[ERROR] Virtual environment was not found." -ForegroundColor Red
    Write-Host "[INFO] Run .\scripts\setup_venv.ps1 first." -ForegroundColor Cyan
    exit 1
}

if (-not (Test-Path $AppPath)) {
    Write-Host "[ERROR] App file was not found: src\pointer_finder.py" -ForegroundColor Red
    exit 1
}

& $VenvActivate

if ($Cli) {
    python $AppPath --cli @AppArgs
}
else {
    python $AppPath --gui @AppArgs
}
