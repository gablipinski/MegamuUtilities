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

# Always use the explicit venv Python to avoid PATH ambiguity
$VenvRoot = Join-Path $ProjectRoot 'venv'
$PythonExe = Join-Path $VenvRoot 'Scripts\python.exe'
if (-not (Test-Path $PythonExe)) {
    Write-Error "[✗] Python not found at $PythonExe. Run setup_venv.ps1 first."
    exit 1
}
Write-Host "[✓] Using Python: $PythonExe" -ForegroundColor Green

Write-Host '[⏳] Installing/updating build dependency (pyinstaller)...' -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pyinstaller

Write-Host '[⏳] Preparing bin folders...' -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Force -Path $SpecDir | Out-Null

# Generate a proper .spec file so collect_all() runs inside Python (more reliable than CLI flags)
$SpecFile = Join-Path $SpecDir "$ExeName.spec"
$SrcDir = Join-Path $ProjectRoot 'src'
$MainPy = Join-Path $SrcDir 'main.py'

Write-Host '[⏳] Generating .spec file...' -ForegroundColor Cyan
$SpecContent = @"
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect every submodule + data file from twitchio and its key dependencies
tw_d,  tw_b,  tw_h  = collect_all('twitchio')
aio_d, aio_b, aio_h = collect_all('aiohttp')
cn_d,  cn_b,  cn_h  = collect_all('charset_normalizer')
yr_d,  yr_b,  yr_h  = collect_all('yarl')
md_d,  md_b,  md_h  = collect_all('multidict')

all_datas    = tw_d  + aio_d + cn_d + yr_d + md_d
all_binaries = tw_b  + aio_b + cn_b + yr_b + md_b
all_hidden   = tw_h  + aio_h + cn_h + yr_h + md_h + [
    'twitchio',
    'twitchio.ext',
    'twitchio.ext.commands',
    'twitchio.ext.commands.core',
    'twitchio.ext.commands.bot',
    'aiohttp',
    'aiohttp.web',
    'aiohttp._websocket',
    'aiohttp.client',
    'aiohttp.connector',
    'frozenlist',
    'aiosignal',
    'async_timeout',
    'certifi',
    'ssl',
    'json',
]

a = Analysis(
    [r'$MainPy'],
    pathex=[r'$SrcDir'],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='$ExeName',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"@
Set-Content -Path $SpecFile -Value $SpecContent -Encoding UTF8

Write-Host '[⏳] Building EXE from .spec file...' -ForegroundColor Cyan
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistDir `
    --workpath $BuildDir `
    $SpecFile

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
