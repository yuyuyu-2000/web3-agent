# ChainCloud-AI local prerequisite checker for Windows PowerShell
# Run from repository root:
#   powershell -ExecutionPolicy Bypass -File scripts\check_local_prereqs.ps1

$ErrorActionPreference = "Stop"

Write-Host "== ChainCloud-AI Windows local prerequisite check =="

function Check-Command {
    param([string]$Name, [string]$Command)
    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if ($null -ne $cmd) {
        Write-Host "OK: $Name found: $($cmd.Source)"
        return $true
    } else {
        Write-Host "MISSING: $Name ($Command)"
        return $false
    }
}

function Test-PortInUse {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $success = $async.AsyncWaitHandle.WaitOne(300)
        if ($success -and $client.Connected) {
            $client.EndConnect($async)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Parse-Version {
    param([string]$Raw)
    $m = [regex]::Match($Raw, "(\d+)\.(\d+)\.(\d+)")
    if ($m.Success) {
        return [version]("$($m.Groups[1].Value).$($m.Groups[2].Value).$($m.Groups[3].Value)")
    }
    return $null
}

$hasUv = Check-Command "uv" "uv"
$hasNode = Check-Command "node" "node"
$hasNpm = Check-Command "npm" "npm"
$hasDocker = Check-Command "docker" "docker"

if ($hasNode) {
    $nodeRaw = (& node --version)
    $nodeVersion = Parse-Version $nodeRaw
    if ($null -ne $nodeVersion -and $nodeVersion -ge [version]"20.19.0") {
        Write-Host "OK: Node.js version is $nodeRaw"
    } else {
        Write-Host "WARN: Node.js version is $nodeRaw. Recommended: Node.js 22 LTS; minimum: 20.19+."
    }
}

if ($hasUv) {
    try {
        $pyRaw = (& uv run python --version 2>$null)
        $pyVersion = Parse-Version $pyRaw
        if ($null -ne $pyVersion -and $pyVersion -ge [version]"3.11.0" -and $pyVersion -lt [version]"3.14.0") {
            Write-Host "OK: uv Python version is $pyRaw"
        } else {
            Write-Host "WARN: uv Python version is $pyRaw. Recommended: Python 3.11 or 3.12."
        }
    } catch {
        Write-Host "INFO: Could not check uv Python version yet."
    }
}

if ($hasDocker) {
    try {
        docker compose version | Out-Null
        Write-Host "OK: docker compose found"
    } catch {
        Write-Host "MISSING: docker compose"
    }

    try {
        docker info | Out-Null
        Write-Host "OK: Docker daemon is running"
    } catch {
        Write-Host "ERROR: Docker daemon is not running. Start Docker Desktop first, then retry."
    }
}

if (Test-Path ".env") {
    Write-Host "OK: .env exists"
} else {
    Write-Host "MISSING: .env. Copy .env.docker.example or .env.example to .env and fill required settings."
}

if (Test-Path "docs\sql\init_memory_tables.sql") {
    Write-Host "OK: docs\sql\init_memory_tables.sql exists"
} else {
    Write-Host "MISSING: docs\sql\init_memory_tables.sql"
}

if (Test-Path "frontend\chaincloud-agent-web") {
    Write-Host "OK: frontend\chaincloud-agent-web exists"
} else {
    Write-Host "MISSING: frontend\chaincloud-agent-web"
}

Write-Host ""
Write-Host "Port checks:"
foreach ($port in @(15432, 8001, 5173)) {
    if (Test-PortInUse -Port $port) {
        Write-Host "INFO: port $port is already in use."
    } else {
        Write-Host "OK: port $port is available."
    }
}
