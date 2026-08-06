param(
    [string]$TargetHost = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param(
        [string]$HostName,
        [int]$Port
    )

    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800)
        if (-not $ok) {
            $tcp.Close()
            return $false
        }

        $tcp.EndConnect($iar)
        $tcp.Close()
        return $true
    }
    catch {
        return $false
    }
}

Write-Host "[Gatekeeper] Cloudflare Tunnel (random URL)" -ForegroundColor Cyan
Write-Host "Target backend: http://${TargetHost}:$Port"
Write-Host ""

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
$docker = Get-Command docker -ErrorAction SilentlyContinue

$runner = $null
$tunnelUrl = "http://${TargetHost}:$Port"

if ($cloudflared) {
    $runner = 'native'
} elseif ($docker) {
    $runner = 'docker'
    if ($TargetHost -eq '127.0.0.1' -or $TargetHost -eq 'localhost') {
        $tunnelUrl = "http://host.docker.internal:$Port"
    }
    Write-Warning "cloudflared nao encontrado no PATH. Usando container cloudflare/cloudflared via Docker."
} else {
    Write-Error "Nem cloudflared nem docker estao disponiveis no PATH. Instale cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
}

if (-not (Test-PortOpen -HostName $TargetHost -Port $Port)) {
    Write-Warning "Nao consegui conectar em http://${TargetHost}:$Port"
    Write-Warning "Inicie o backend antes (ex: .\\scripts\\start-gatekeeper.ps1)."
    Write-Host ""
}

Write-Host "Iniciando tunnel... (Ctrl+C para encerrar)" -ForegroundColor Yellow
Write-Host "Quando conectar, procure pela URL https://<random>.trycloudflare.com no log abaixo." -ForegroundColor DarkYellow
Write-Host ""

if ($runner -eq 'native') {
    & $cloudflared.Source tunnel --url $tunnelUrl --no-autoupdate
} else {
    & docker run --rm -it cloudflare/cloudflared:latest tunnel --url $tunnelUrl --no-autoupdate
}
