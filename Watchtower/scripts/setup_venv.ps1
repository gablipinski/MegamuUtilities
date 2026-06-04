#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Creates and activates a Python virtual environment for Watchtower
.DESCRIPTION
    This script creates a new Python virtual environment in the 'venv' folder
    and activates it automatically at the end. It also ensures Inno Setup 6
    is available so installer builds can run.
.EXAMPLE
    .\setup_venv.ps1
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot "venv"
$VenvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"

function Find-Python312Executable {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Python312\python.exe')
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
        $pythonPaths = & where.exe python 2>$null
        foreach ($pythonPath in $pythonPaths) {
            if ($pythonPath -match 'Python312') {
                return ($pythonPath | Select-Object -First 1)
            }
        }
    }
    catch {
        # Python is not available in PATH.
    }

    return $null
}

function Get-PreferredPythonCommand {
    # Prefer Python 3.12 for better PyTorch CUDA wheel compatibility.
    $python312Exe = Find-Python312Executable
    if ($python312Exe) {
        return @{ Exe = $python312Exe; Args = @() }
    }

    try {
        $null = Get-Command py -ErrorAction Stop
        & py -3.12 -c "import sys; print(sys.version)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = 'py'; Args = @('-3.12') }
        }
    }
    catch {
        # No 'py' launcher available; continue with fallback.
    }

    return @{ Exe = 'python'; Args = @() }
}

function Get-VenvPythonVersion {
    param(
        [string]$PythonExe
    )

    try {
        $version = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        return ($version | Select-Object -First 1)
    }
    catch {
        return $null
    }
}

function Test-NvidiaCudaAvailable {
    try {
        $null = Get-Command nvidia-smi -ErrorAction Stop
        $gpuOutput = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        return -not [string]::IsNullOrWhiteSpace(($gpuOutput | Select-Object -First 1))
    }
    catch {
        return $false
    }
}

function Install-TorchRuntime {
    param(
        [string]$PythonExe
    )

    function Try-InstallTorchFromIndex {
        param(
            [string]$IndexUrl,
            [string]$Label
        )

        Write-Host "[INFO] Trying to install PyTorch ($Label)..." -ForegroundColor Cyan
        & $PythonExe -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $IndexUrl
        return ($LASTEXITCODE -eq 0)
    }

    function Validate-Torch {
        & $PythonExe -c "import torch; print('torch:', torch.__version__); print('cuda_available:', torch.cuda.is_available()); print('cuda_version:', torch.version.cuda); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"
        return ($LASTEXITCODE -eq 0)
    }

    $hasNvidiaCuda = Test-NvidiaCudaAvailable
    $cudaInstalled = $false

    Write-Host "[INFO] Venv Python version:" -ForegroundColor Cyan
    & $PythonExe -c "import sys; print(sys.version.split()[0])"

    if ($hasNvidiaCuda) {
        Write-Host "[INFO] NVIDIA detected. Looking for a compatible CUDA wheel..." -ForegroundColor Cyan

        $cudaIndexes = @(
            @{ Url = 'https://download.pytorch.org/whl/cu126'; Label = 'CUDA cu126' },
            @{ Url = 'https://download.pytorch.org/whl/cu124'; Label = 'CUDA cu124' },
            @{ Url = 'https://download.pytorch.org/whl/cu121'; Label = 'CUDA cu121' },
            @{ Url = 'https://download.pytorch.org/whl/cu118'; Label = 'CUDA cu118' }
        )

        foreach ($cudaIndex in $cudaIndexes) {
            if (Try-InstallTorchFromIndex -IndexUrl $cudaIndex.Url -Label $cudaIndex.Label) {
                if (Validate-Torch) {
                    $cudaInstalled = $true
                    break
                }
            }
        }

        if (-not $cudaInstalled) {
            Write-Host "[WARN] No compatible CUDA wheel found for this Python version. Falling back to CPU." -ForegroundColor Yellow
        }
    }

    if (-not $cudaInstalled) {
        Write-Host "[INFO] CUDA not detected. Installing CPU PyTorch..." -ForegroundColor Cyan
        $cpuInstalled = Try-InstallTorchFromIndex -IndexUrl 'https://download.pytorch.org/whl/cpu' -Label 'CPU'

        if (-not $cpuInstalled) {
            Write-Host "[WARN] CPU index installation failed. Trying standard PyPI..." -ForegroundColor Yellow
            & $PythonExe -m pip install --upgrade --force-reinstall torch torchvision torchaudio
            $cpuInstalled = ($LASTEXITCODE -eq 0)
        }

        if (-not $cpuInstalled) {
            Write-Host "[ERROR] Failed to install PyTorch (OCR runtime)." -ForegroundColor Red
            Write-Host "[INFO] Tip: your Python version may not have an available wheel. Try Python 3.12." -ForegroundColor Cyan
            exit 1
        }
    }

    Write-Host "[INFO] Validating PyTorch backend..." -ForegroundColor Cyan
    if (-not (Validate-Torch)) {
        Write-Host "[ERROR] Failed to validate PyTorch installation." -ForegroundColor Red
        exit 1
    }
}

function Get-InnoCompilerPath {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

function Ensure-InnoSetupInstalled {
    $innoPath = Get-InnoCompilerPath
    if ($innoPath) {
        Write-Host "[OK] Inno Setup detected: $innoPath" -ForegroundColor Green
        return
    }

    Write-Host "`n[INFO] Inno Setup 6 was not found. Installing with winget..." -ForegroundColor Cyan

    try {
        $null = Get-Command winget -ErrorAction Stop
    }
    catch {
        Write-Host "[WARN] winget is not available. Install Inno Setup manually:" -ForegroundColor Yellow
        Write-Host "       https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
        return
    }

    winget install --id JRSoftware.InnoSetup --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] Failed to install Inno Setup automatically (exit code $LASTEXITCODE)." -ForegroundColor Yellow
        Write-Host "       Install manually from: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
        return
    }

    $innoPath = Get-InnoCompilerPath
    if ($innoPath) {
        Write-Host "[OK] Inno Setup installed: $innoPath" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] Inno Setup install finished, but ISCC.exe was not found in default paths." -ForegroundColor Yellow
        Write-Host "       Verify installation and rerun setup/build if needed." -ForegroundColor Yellow
    }
}

Write-Host "`n[INFO] Creating Python virtual environment for Watchtower..." -ForegroundColor Cyan

# Check whether a venv already exists
if (Test-Path $VenvPath) {
    Write-Host "[WARN] Virtual environment already exists." -ForegroundColor Yellow
    Write-Host "[INFO] Reusing existing venv and syncing OCR runtime (CUDA/CPU)." -ForegroundColor Cyan

    if (Test-Path $VenvPython) {
        $existingVersion = Get-VenvPythonVersion -PythonExe $VenvPython
        if ($existingVersion -and $existingVersion -ne '3.12') {
            Write-Host "[WARN] Current venv uses Python $existingVersion." -ForegroundColor Yellow
            Write-Host "[INFO] For better CUDA compatibility, recreate it with Python 3.12:" -ForegroundColor Cyan
            Write-Host "      Remove-Item -Recurse -Force .\venv" -ForegroundColor Cyan
            Write-Host "      .\scripts\setup_venv.ps1" -ForegroundColor Cyan
        }
    }
}

if (-not (Test-Path $VenvPath)) {
    # Create the venv
    $pythonCmd = Get-PreferredPythonCommand
    if ($pythonCmd.Exe -eq 'py' -or $pythonCmd.Exe -match 'Python312') {
        Write-Host "[INFO] Creating venv with Python 3.12 (preferred for CUDA)..." -ForegroundColor Cyan
    }
    else {
        Write-Host "[INFO] Creating venv with default Python..." -ForegroundColor Cyan
        Write-Host "[WARN] Python 3.12 was not found automatically. For better CUDA compatibility, install Python 3.12." -ForegroundColor Yellow
    }

    & $pythonCmd.Exe @($pythonCmd.Args + @('-m', 'venv', $VenvPath))

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }

    Write-Host "[OK] Virtual environment created successfully." -ForegroundColor Green
}

# Activate venv
Write-Host "[INFO] Activating virtual environment..." -ForegroundColor Cyan
& $VenvActivate

# Install dependencies
Write-Host "`n[INFO] Installing dependencies..." -ForegroundColor Cyan
pip install -r $RequirementsPath

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Failed to install dependencies." -ForegroundColor Red
    exit 1
}

Install-TorchRuntime -PythonExe $VenvPython
Ensure-InnoSetupInstalled

Write-Host "`n[OK] Setup completed successfully." -ForegroundColor Green
Write-Host "[INFO] To activate the venv again, run: .\scripts\activate_venv.ps1" -ForegroundColor Cyan
Write-Host "[INFO] To run the monitor, run: .\scripts\run_watchtower.ps1`n" -ForegroundColor Cyan
