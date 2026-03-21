# Build script for creating a standalone EXE in Live_Monitor\bin
# Usage: .\scripts\build_exe.ps1

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BinDir = Join-Path $ProjectRoot 'bin'
$BuildDir = Join-Path $BinDir 'build'
$SpecDir = Join-Path $BinDir 'spec'
$DistDir = Join-Path $BinDir
$ConfigsSource = Join-Path $ProjectRoot 'configs'
$ConfigsDest = Join-Path $BinDir 'configs'
$ExeName = 'LiveMonitor'

if (-not $env:VIRTUAL_ENV) {
    Write-Host '[⏳] Activating virtual environment...' -ForegroundColor Cyan
    & "$ProjectRoot\venv\Scripts\Activate.ps1"
}

Write-Host '[⏳] Installing/updating build dependency (pyinstaller)...' -ForegroundColor Cyan
python -m pip install --upgrade pyinstaller

Write-Host '[⏳] Preparing bin folders...' -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Force -Path $SpecDir | Out-Null

Write-Host '[⏳] Building EXE with PyInstaller...' -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name $ExeName `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $SpecDir `
    "$ProjectRoot\src\main.py"

Write-Host '[⏳] Copying editable configs to bin\configs...' -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $ConfigsDest | Out-Null
Copy-Item -Force (Join-Path $ConfigsSource 'config.json') (Join-Path $ConfigsDest 'config.json')
if (Test-Path (Join-Path $ConfigsSource 'config_example.json')) {
    Copy-Item -Force (Join-Path $ConfigsSource 'config_example.json') (Join-Path $ConfigsDest 'config_example.json')
}

Write-Host ''
Write-Host "[✓] Build complete: $DistDir\$ExeName.exe" -ForegroundColor Green
Write-Host "[✓] Editable config: $ConfigsDest\config.json" -ForegroundColor Green
Write-Host ''
Write-Host 'Run:' -ForegroundColor Yellow
Write-Host "  cd $BinDir" -ForegroundColor Yellow
Write-Host "  .\$ExeName.exe" -ForegroundColor Yellow
