#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Full build pipeline: compile Watchtower.exe and create a Windows installer.

.DESCRIPTION
    Step 1 — Validate prerequisites (venv, private key)
    Step 2 — Install / upgrade build dependencies (Nuitka, etc.)
    Step 3 — Compile src/main.py → dist/Watchtower.exe  (Nuitka native binary)
    Step 4 — Create installer_output/Watchtower_Setup_1.0.0.exe  (Inno Setup 6)

.PARAMETER SkipInstaller
    Skip the Inno Setup step (useful for quick iteration builds).

.PARAMETER Clean
    Delete dist/ and build/ before compiling (default: true).

.EXAMPLE
    .\scripts\build_exe.ps1
    .\scripts\build_exe.ps1 -SkipInstaller
    .\scripts\build_exe.ps1 -Clean:$false -SkipInstaller
#>

param(
    [switch]$SkipInstaller = $false,
    [switch]$Clean = $true
)

$ErrorActionPreference = 'Stop'

$ProjectRoot    = Split-Path -Parent $PSScriptRoot
$VenvActivate   = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$MainPath       = Join-Path $ProjectRoot "src\main.py"
$DistDir        = Join-Path $ProjectRoot "dist"
$BuildDir       = Join-Path $ProjectRoot "build"
$InstallerOut   = Join-Path $ProjectRoot "installer_output"
$SetupIss       = Join-Path $ProjectRoot "installer\setup.iss"
$PrivateKeyPath = Join-Path $ProjectRoot "licenses\keys\private_key.pem"
$IconPngPath    = Join-Path $ProjectRoot "icons\watchtower.png"
$IconIcoPath    = Join-Path $ProjectRoot "icons\watchtower.ico"
$InnoCompiler   = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Watchtower - Build Pipeline           " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# ── Preflight checks ───────────────────────────────────────────────────────────

if (-not (Test-Path $VenvActivate)) {
    Write-Host "[X] Virtual environment not found." -ForegroundColor Red
    Write-Host "    Run .\scripts\setup_venv.ps1 first." -ForegroundColor Cyan
    exit 1
}

if (-not (Test-Path $MainPath)) {
    Write-Host "[X] src\main.py not found." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $PrivateKeyPath)) {
    Write-Host "[X] Private key not found: $PrivateKeyPath" -ForegroundColor Red
    Write-Host "    Run: python tools\generate_keys.py" -ForegroundColor Cyan
    Write-Host "    This embeds the public key in src\license_manager.py" -ForegroundColor Cyan
    exit 1
}

& $VenvActivate

# ── Step 1: Clean previous artifacts ──────────────────────────────────────────

if ($Clean) {
    Write-Host "[1/4] Cleaning previous build artifacts..." -ForegroundColor Cyan
    if (Test-Path $DistDir)  { Remove-Item -Recurse -Force $DistDir }
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
} else {
    Write-Host "[1/4] Skipping clean (-Clean:`$false)." -ForegroundColor DarkGray
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# ── Step 2: Install build dependencies ────────────────────────────────────────

Write-Host "[2/4] Updating build dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip --quiet
python -m pip install --upgrade nuitka ordered-set zstandard pillow --quiet

if (-not (Test-Path $IconPngPath)) {
    Write-Host "[X] Icon source not found: $IconPngPath" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $IconIcoPath)) {
    Write-Host "[2/4] Creating icons\watchtower.ico from PNG..." -ForegroundColor Cyan
    python -c "from PIL import Image; Image.open(r'$IconPngPath').save(r'$IconIcoPath', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $IconIcoPath)) {
        Write-Host "[X] Failed to generate ICO file from PNG icon." -ForegroundColor Red
        exit 1
    }
}

# ── Step 3: Compile with Nuitka ────────────────────────────────────────────────

Write-Host "[3/4] Compiling with Nuitka (this may take a few minutes)..." -ForegroundColor Cyan

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
    --include-package=cryptography `
    --windows-icon-from-ico="$IconIcoPath" `
    --output-dir="$DistDir" `
    --output-filename="Watchtower.exe" `
    "$MainPath"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] Compilation failed (exit code $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[OK] Executable: $DistDir\Watchtower.exe" -ForegroundColor Green

# ── Step 4: Create installer with Inno Setup ──────────────────────────────────

if ($SkipInstaller) {
    Write-Host "[4/4] Installer skipped (-SkipInstaller)." -ForegroundColor DarkGray
}
elseif (-not (Test-Path $InnoCompiler)) {
    Write-Host "[4/4] Inno Setup 6 not found - skipping installer." -ForegroundColor Yellow
    Write-Host "      Install from: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    Write-Host "      Then re-run, or run manually:" -ForegroundColor Yellow
    Write-Host "      `"$InnoCompiler`" `"$SetupIss`"" -ForegroundColor Yellow
}
else {
    Write-Host "[4/4] Creating installer with Inno Setup 6..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $InstallerOut | Out-Null

    & "$InnoCompiler" "$SetupIss"

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] Inno Setup failed (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "[OK] Installer: $InstallerOut\Watchtower_Setup_1.0.0.exe" -ForegroundColor Green
}

# ── Summary ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Build Complete                        " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Outputs:" -ForegroundColor Cyan
Write-Host "  Executable : $DistDir\Watchtower.exe"

if (-not $SkipInstaller -and (Test-Path $InnoCompiler)) {
    Write-Host "  Installer  : $InstallerOut\Watchtower_Setup_1.0.0.exe"
}

Write-Host ""
Write-Host "Distribution checklist:" -ForegroundColor Yellow
Write-Host "  1. Share Watchtower_Setup_1.0.0.exe with the user"
Write-Host "  2. User installs and launches the app"
Write-Host "  3. App shows their Machine ID - they send it to you"
Write-Host "  4. You run:  python tools\generate_license.py <machine_id> ""<name>"""
Write-Host "  5. You send the generated license.dat to the user"
Write-Host "  6. User places license.dat in: %APPDATA%\Watchtower\"
Write-Host ""
