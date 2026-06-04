# Script para criar venv e instalar dependências
# Uso: .\scripts\setup_venv.ps1

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

    return $null
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

$PythonCmd = Get-PreferredPythonCommand

Write-Host "`n[INFO] Criando ambiente virtual para Guardtower..." -ForegroundColor Cyan

if (-not $PythonCmd) {
    Write-Host "[ERROR] Python 3.12 nao encontrado neste sistema." -ForegroundColor Red
    Write-Host "[INFO] Instale Python 3.12 e execute novamente .\scripts\setup_venv.ps1" -ForegroundColor Cyan
    exit 1
}

if (Test-Path $VenvPath) {
    Write-Host "[WARN] Ambiente virtual ja existe." -ForegroundColor Yellow
    if (Test-Path $VenvPython) {
        $existingVersion = Get-VenvPythonVersion -PythonExe $VenvPython
        if ($existingVersion -and $existingVersion -ne '3.12') {
            Write-Host "[WARN] O venv atual usa Python $existingVersion." -ForegroundColor Yellow
            Write-Host "[INFO] Recriando o venv com Python 3.12..." -ForegroundColor Cyan
            Remove-Item -Recurse -Force $VenvPath
        }
    }
}

if (-not (Test-Path $VenvPath)) {
    Write-Host "[INFO] Criando venv com Python 3.12..." -ForegroundColor Cyan

    & $PythonCmd.Exe @($PythonCmd.Args + @('-m', 'venv', $VenvPath))

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Falha ao criar ambiente virtual." -ForegroundColor Red
        exit 1
    }

    Write-Host "[OK] Ambiente virtual criado com sucesso." -ForegroundColor Green
}

$createdVersion = Get-VenvPythonVersion -PythonExe $VenvPython
if ($createdVersion -ne '3.12') {
    Write-Host "[ERROR] Venv criado com Python $createdVersion, mas o projeto exige 3.12." -ForegroundColor Red
    exit 1
}

Write-Host "[INFO] Ativando ambiente virtual..." -ForegroundColor Cyan
& $VenvActivate

Write-Host "`n[INFO] Instalando dependencias..." -ForegroundColor Cyan
pip install -r $RequirementsPath --upgrade

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Falha ao instalar dependencias." -ForegroundColor Red
    exit 1
}

Write-Host "`n[OK] Setup concluido com sucesso." -ForegroundColor Green
Write-Host "[INFO] Para ativar o venv novamente: .\scripts\activate_venv.ps1" -ForegroundColor Cyan
Write-Host "[INFO] Para iniciar o bot: .\scripts\run_bot.ps1`n" -ForegroundColor Cyan
