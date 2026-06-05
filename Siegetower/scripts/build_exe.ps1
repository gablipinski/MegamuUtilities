#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Full build pipeline: compile Siegetower.exe and optionally create an installer.

.DESCRIPTION
    Step 1 - Validate prerequisites (venv, private key)
    Step 2 - Install / upgrade build dependencies
    Step 3 - Compile src/main.py -> dist/Siegetower.exe (Nuitka)
    Step 4 - Create installer (only if installer/setup.iss exists and Inno Setup is installed)
#>

param(
    [switch]$SkipInstaller = $false,
    [switch]$Clean = $true
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SyncReleaseScript = Join-Path $ProjectRoot 'scripts\sync_release.ps1'
$VenvActivate = Join-Path $ProjectRoot '.venv\Scripts\Activate.ps1'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ReleaseInfoPath = Join-Path $ProjectRoot 'release_info.json'
$MainPath = Join-Path $ProjectRoot 'src\main.py'
$DistDir = Join-Path $ProjectRoot 'dist'
$BuildDir = Join-Path $ProjectRoot 'build'
$InstallerOut = Join-Path $ProjectRoot 'installer_output'
$SetupIss = Join-Path $ProjectRoot 'installer\setup.iss'
$PrivateKeyPath = Join-Path $ProjectRoot 'licenses\keys\private_key.pem'
$IconPngPath = Join-Path $ProjectRoot 'icons\siegetower.png'
$IconIcoPath = Join-Path $ProjectRoot 'icons\siegetower.ico'

function Get-AppMetadata {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Release metadata file not found: $Path"
    }

    $release = Get-Content -Raw -Path $Path | ConvertFrom-Json
    if (-not $release.app_name -or -not $release.version -or -not $release.publisher) {
        throw 'release_info.json must contain app_name, version, and publisher'
    }

    return @{
        Name = [string]$release.app_name
        Version = [string]$release.version
        Publisher = [string]$release.publisher
    }
}

function Convert-ToWindowsVersion {
    param([string]$Version)

    if ($Version -match '^(\d+)\.(\d+)\.(\d+)\.(\d+)$') {
        return $Version
    }

    if ($Version -match '^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$') {
        return "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"
    }

    throw "Unsupported version format for Windows metadata: $Version"
}

function Get-InnoCompilerPath {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramW6432 'Inno Setup 6\ISCC.exe')
    )

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    try {
        $cmd = Get-Command ISCC.exe -ErrorAction Stop
        if ($cmd -and (Test-Path $cmd.Source)) {
            return $cmd.Source
        }
    }
    catch {
    }

    return $null
}

$InnoCompiler = Get-InnoCompilerPath

if (Test-Path $SyncReleaseScript) {
    try {
        & $SyncReleaseScript
        if (-not $?) {
            throw 'sync_release.ps1 reported failure.'
        }
    }
    catch {
        Write-Host '[X] Failed to sync release metadata.' -ForegroundColor Red
        Write-Host "    $($_.Exception.Message)" -ForegroundColor DarkGray
        exit 1
    }
}

$AppMetadata = Get-AppMetadata -Path $ReleaseInfoPath
$AppName = $AppMetadata.Name
$AppVersion = $AppMetadata.Version
$AppPublisher = $AppMetadata.Publisher
$WindowsVersion = Convert-ToWindowsVersion -Version $AppVersion

if (-not (Test-Path $VenvActivate)) {
    Write-Host '[X] Virtual environment not found.' -ForegroundColor Red
    Write-Host '    Run .\scripts\setup_venv.ps1 first.' -ForegroundColor Cyan
    exit 1
}

if (-not (Test-Path $MainPath)) {
    Write-Host '[X] src\main.py not found.' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $PrivateKeyPath)) {
    Write-Host "[X] Private key not found: $PrivateKeyPath" -ForegroundColor Red
    Write-Host '    Run: python tools\generate_keys.py' -ForegroundColor Cyan
    exit 1
}

& $VenvActivate

if (-not (Test-Path $VenvPython)) {
    Write-Host "[X] Python executable not found in venv: $VenvPython" -ForegroundColor Red
    exit 1
}

if ($Clean) {
    Write-Host '[1/4] Cleaning previous build artifacts...' -ForegroundColor Cyan
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

Write-Host '[2/4] Updating build dependencies...' -ForegroundColor Cyan
& "$VenvPython" -m pip install --upgrade pip --quiet
& "$VenvPython" -m pip install --upgrade nuitka ordered-set zstandard pillow --quiet

if (-not (Test-Path $IconPngPath)) {
    Write-Host "[X] Icon source not found: $IconPngPath" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $IconIcoPath)) {
    Write-Host '[2/4] Creating icons\siegetower.ico from PNG...' -ForegroundColor Cyan
    & "$VenvPython" -c "from PIL import Image; Image.open(r'$IconPngPath').save(r'$IconIcoPath', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $IconIcoPath)) {
        Write-Host '[X] Failed to generate ICO file from PNG icon.' -ForegroundColor Red
        exit 1
    }
}

Write-Host '[3/4] Compiling with Nuitka (this may take a few minutes)...' -ForegroundColor Cyan

& "$VenvPython" -m nuitka `
    --onefile `
    --standalone `
    --assume-yes-for-downloads `
    --remove-output `
    --lto=yes `
    --python-flag=no_docstrings `
    --python-flag=-O `
    --enable-plugin=tk-inter `
    --windows-console-mode=disable `
    --company-name="$AppPublisher" `
    --product-name="$AppName" `
    --file-description="$AppName" `
    --file-version="$WindowsVersion" `
    --product-version="$WindowsVersion" `
    --include-package=pyautogui `
    --include-package=pyscreeze `
    --include-package=mouseinfo `
    --include-package=cryptography `
    --windows-icon-from-ico="$IconIcoPath" `
    --output-dir="$DistDir" `
    --output-filename="$AppName.exe" `
    "$MainPath"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] Compilation failed (exit code $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[OK] Executable: $DistDir\\$AppName.exe" -ForegroundColor Green

if ($SkipInstaller) {
    Write-Host '[4/4] Installer skipped (-SkipInstaller).' -ForegroundColor DarkGray
}
elseif (-not (Test-Path $SetupIss)) {
    Write-Host '[4/4] installer/setup.iss not found - skipping installer.' -ForegroundColor Yellow
}
elseif (-not $InnoCompiler) {
    Write-Host '[4/4] Inno Setup 6 not found - skipping installer.' -ForegroundColor Yellow
}
else {
    Write-Host '[4/4] Creating installer with Inno Setup 6...' -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $InstallerOut | Out-Null

    & "$InnoCompiler" "/DMyAppName=$AppName" "/DMyAppVersion=$AppVersion" "/DMyAppPublisher=$AppPublisher" "$SetupIss"

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] Inno Setup failed (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    Write-Host "[OK] Installer: $InstallerOut\\${AppName}_Setup_$AppVersion.exe" -ForegroundColor Green
}

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host '  Build Complete                        ' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
