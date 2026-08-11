# ChainCloud-AI backend + frontend local launcher for Windows PowerShell
# Run from repository root:
#   powershell -ExecutionPolicy Bypass -File scripts\start_chaincloud_agent_web.ps1

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Frontend = Join-Path $Root "frontend\chaincloud-agent-web"

Write-Host "ChainCloud Agent Windows local launcher"
Write-Host "Backend:  $Root"
Write-Host "Frontend: $Frontend"

if (!(Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: uv is not installed or not in PATH."
    exit 1
}

if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: npm is not installed or not in PATH."
    exit 1
}

if (!(Test-Path (Join-Path $Root ".env"))) {
    Write-Host "WARNING: .env not found in repository root."
    Write-Host "Please copy .env.docker.example to .env and fill API keys and database settings."
}

if (!(Test-Path $Frontend)) {
    Write-Host "ERROR: frontend directory not found: $Frontend"
    exit 1
}

$backendCommand = "cd `"$Root`"; uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001"
$frontendCommand = "cd `"$Frontend`"; if (!(Test-Path node_modules)) { npm install }; npm run dev"

Write-Host "Starting backend on http://127.0.0.1:8001 ..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand

Write-Host "Waiting for backend readiness..."
$backendReady = $false
for ($i = 1; $i -le 60; $i++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8001/memory" -TimeoutSec 2 | Out-Null
        $backendReady = $true
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $backendReady) {
    Write-Host "WARN: Backend did not become ready within 120 seconds."
    Write-Host "Frontend will still be started, but initial proxy requests may fail until backend is ready."
}

Write-Host "Starting frontend on http://127.0.0.1:5173 ..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand

Start-Sleep -Seconds 5
Start-Process "http://127.0.0.1:5173"
