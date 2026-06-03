#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Cria e ativa um ambiente virtual Python para Safe Monitor
.DESCRIPTION
    Este script cria um novo ambiente virtual Python na pasta 'venv'
    e ativa automaticamente ao final
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
        # Sem python no PATH.
    }

    return $null
}

function Get-PreferredPythonCommand {
    # Prioriza Python 3.12 por melhor compatibilidade de wheels PyTorch CUDA.
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
        # Sem launcher 'py'; segue fallback.
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

        Write-Host "[⏳] Tentando instalar PyTorch ($Label)..." -ForegroundColor Cyan
        & $PythonExe -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url $IndexUrl
        return ($LASTEXITCODE -eq 0)
    }

    function Validate-Torch {
        & $PythonExe -c "import torch; print('torch:', torch.__version__); print('cuda_available:', torch.cuda.is_available()); print('cuda_version:', torch.version.cuda); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"
        return ($LASTEXITCODE -eq 0)
    }

    $hasNvidiaCuda = Test-NvidiaCudaAvailable
    $cudaInstalled = $false

    Write-Host "[ℹ️]  Python do venv:" -ForegroundColor Cyan
    & $PythonExe -c "import sys; print(sys.version.split()[0])"

    if ($hasNvidiaCuda) {
        Write-Host "[⏳] NVIDIA detectada. Buscando wheel CUDA compativel..." -ForegroundColor Cyan

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
            Write-Host "[⚠️]  Nenhuma wheel CUDA compativel encontrada para este Python. Fazendo fallback para CPU." -ForegroundColor Yellow
        }
    }

    if (-not $cudaInstalled) {
        Write-Host "[ℹ️]  CUDA nao detectado. Instalando PyTorch CPU..." -ForegroundColor Cyan
        $cpuInstalled = Try-InstallTorchFromIndex -IndexUrl 'https://download.pytorch.org/whl/cpu' -Label 'CPU'

        if (-not $cpuInstalled) {
            Write-Host "[⚠️]  Falha no indice CPU dedicado. Tentando PyPI padrao..." -ForegroundColor Yellow
            & $PythonExe -m pip install --upgrade --force-reinstall torch torchvision torchaudio
            $cpuInstalled = ($LASTEXITCODE -eq 0)
        }

        if (-not $cpuInstalled) {
            Write-Host "[✗] Erro ao instalar PyTorch (runtime OCR)!" -ForegroundColor Red
            Write-Host "[ℹ️]  Dica: sua versao de Python pode nao ter wheel disponivel. Tente Python 3.12." -ForegroundColor Cyan
            exit 1
        }
    }

    Write-Host "[⏳] Validando backend do PyTorch..." -ForegroundColor Cyan
    if (-not (Validate-Torch)) {
        Write-Host "[✗] Falha ao validar instalacao do PyTorch!" -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n🐍 Criando ambiente virtual Python para Safe Monitor..." -ForegroundColor Cyan

# Verifica se já existe um venv
if (Test-Path $VenvPath) {
    Write-Host "[⚠️]  Ambiente virtual já existe." -ForegroundColor Yellow
    Write-Host "[ℹ️]  Reaproveitando venv existente e sincronizando runtime OCR (CUDA/CPU)." -ForegroundColor Cyan

    if (Test-Path $VenvPython) {
        $existingVersion = Get-VenvPythonVersion -PythonExe $VenvPython
        if ($existingVersion -and $existingVersion -ne '3.12') {
            Write-Host "[⚠️]  Venv atual usa Python $existingVersion." -ForegroundColor Yellow
            Write-Host "[ℹ️]  Para melhor chance de CUDA, recrie com Python 3.12:" -ForegroundColor Cyan
            Write-Host "      Remove-Item -Recurse -Force .\venv" -ForegroundColor Cyan
            Write-Host "      .\scripts\setup_venv.ps1" -ForegroundColor Cyan
        }
    }
}

if (-not (Test-Path $VenvPath)) {
    # Cria o venv
    $pythonCmd = Get-PreferredPythonCommand
    if ($pythonCmd.Exe -eq 'py' -or $pythonCmd.Exe -match 'Python312') {
        Write-Host "[⏳] Criando venv com Python 3.12 (preferido para CUDA)..." -ForegroundColor Cyan
    }
    else {
        Write-Host "[⏳] Criando venv com Python padrao..." -ForegroundColor Cyan
        Write-Host "[⚠️]  Python 3.12 nao encontrado automaticamente. Para melhor compatibilidade CUDA, instale Python 3.12." -ForegroundColor Yellow
    }

    & $pythonCmd.Exe @($pythonCmd.Args + @('-m', 'venv', $VenvPath))

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[✗] Erro ao criar ambiente virtual!" -ForegroundColor Red
        exit 1
    }

    Write-Host "[✓] Ambiente virtual criado com sucesso!" -ForegroundColor Green
}

# Ativa o venv
Write-Host "[⏳] Ativando ambiente virtual..." -ForegroundColor Cyan
& $VenvActivate

# Instala as dependências
Write-Host "`n[⏳] Instalando dependências..." -ForegroundColor Cyan
pip install -r $RequirementsPath

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[✗] Erro ao instalar dependências!" -ForegroundColor Red
    exit 1
}

Install-TorchRuntime -PythonExe $VenvPython

Write-Host "`n[✓] Setup concluído com sucesso!" -ForegroundColor Green
Write-Host "[ℹ️]  Para ativar o venv novamente, execute: .\scripts\activate_venv.ps1" -ForegroundColor Cyan
Write-Host "[ℹ️]  Para rodar o monitor, execute: .\scripts\run_monitor.ps1`n" -ForegroundColor Cyan
