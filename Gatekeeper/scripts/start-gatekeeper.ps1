# Startup script for Gatekeeper with automatic host MAC detection
# Run this script instead of "docker compose up" to auto-detect your MAC addresses

param(
    [switch]$Build,
    [switch]$Detach
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir

Write-Host "🔐 Gatekeeper Startup Script" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Detect host MAC addresses
Write-Host "📍 Detecting host MAC addresses..." -ForegroundColor Yellow
$adapters = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' }

if ($adapters.Count -eq 0) {
    Write-Host "⚠️  No active network adapters found - MAC filtering disabled" -ForegroundColor Yellow
    $enforceMAC = "0"
    $allowedMACs = ""
} else {
    $macs = @()
    foreach ($adapter in $adapters) {
        $mac = $adapter.MacAddress
        if ($mac) {
            # Convert from Windows format (XX-XX-XX-XX-XX-XX) to standard format (XX:XX:XX:XX:XX:XX)
            $normalizedMac = $mac -replace '-', ':'
            $macs += $normalizedMac
            Write-Host "  ✓ $($adapter.Name): $normalizedMac" -ForegroundColor Green
        }
    }
    
    $enforceMAC = "1"
    $allowedMACs = $macs -join ','
    Write-Host "Allowed MACs: $allowedMACs" -ForegroundColor Green
}

Write-Host ""

# Create temporary .env file for this startup only
$envFile = "$projectDir\.env"
$envContent = @"
GATEKEEPER_ENFORCE_ADMIN_MAC=$enforceMAC
GATEKEEPER_ADMIN_ALLOWED_MACS=$allowedMACs
GATEKEEPER_HOST=0.0.0.0
GATEKEEPER_PORT=8000
GATEKEEPER_WORKSPACE_ROOT=/workspace
GATEKEEPER_SECRET_KEY=change-me-before-production
GATEKEEPER_BOOTSTRAP_ADMIN_EMAIL=admin
GATEKEEPER_BOOTSTRAP_ADMIN_PASSWORD=admin
GATEKEEPER_DEFAULT_LICENSE_DAYS=30
"@

Set-Content -Path $envFile -Value $envContent -Encoding UTF8
Write-Host "Generated temporary .env with detected MACs" -ForegroundColor Green

Write-Host ""

# Build and start Docker container
$dockerArgs = @('compose', '-f', "$projectDir\docker-compose.yml")

if ($Build) {
    $dockerArgs += @('up', '-d', '--build')
    Write-Host "🐳 Building and starting Gatekeeper container..." -ForegroundColor Yellow
} else {
    if ($Detach) {
        $dockerArgs += @('up', '-d')
    } else {
        $dockerArgs += @('up')
    }
    Write-Host "🐳 Starting Gatekeeper container..." -ForegroundColor Yellow
}

& docker @dockerArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ Gatekeeper is running!" -ForegroundColor Green
    Write-Host "🌐 Access at: http://localhost:8000" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "❌ Failed to start Gatekeeper" -ForegroundColor Red
    exit 1
}
