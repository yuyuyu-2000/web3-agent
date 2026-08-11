# ChainCloud-AI local Docker PostgreSQL setup for Windows PowerShell
# Run from repository root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local_postgres.ps1

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$Service = if ($env:POSTGRES_COMPOSE_SERVICE) { $env:POSTGRES_COMPOSE_SERVICE } else { "postgres" }

Write-Host "== ChainCloud-AI Windows local PostgreSQL setup =="
Write-Host "Repository: $Root"

if (!(Test-Path "docs\sql\init_memory_tables.sql")) {
    Write-Host "ERROR: docs\sql\init_memory_tables.sql not found."
    exit 1
}

try {
    docker --version | Out-Null
    docker compose version | Out-Null
} catch {
    Write-Host "ERROR: Docker or Docker Compose is not available."
    Write-Host "Install Docker Desktop for Windows first."
    exit 1
}

try {
    docker info | Out-Null
} catch {
    Write-Host "ERROR: Docker Desktop is not running."
    Write-Host "Please start Docker Desktop and wait until the engine is running, then retry."
    exit 1
}

Write-Host "Starting Docker PostgreSQL service..."
docker compose up -d $Service
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to start Docker PostgreSQL service."
    Write-Host "Please check Docker Desktop and retry."
    exit 1
}

Write-Host "Waiting for PostgreSQL to become ready..."
$ready = $false
for ($i = 1; $i -le 40; $i++) {
    docker compose exec -T $Service pg_isready -U chaincloud -d chaincloud_memory_dev *> $null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    Write-Host "ERROR: PostgreSQL did not become ready in time."
    docker compose ps
    exit 1
}

Write-Host "PostgreSQL is ready."
Write-Host "Initializing memory table through Docker PostgreSQL..."
Get-Content "docs\sql\init_memory_tables.sql" | docker compose exec -T $Service psql -U chaincloud -d chaincloud_memory_dev

if (Test-Path "docs\sql\init_auth_tables.sql") {
    Write-Host "Initializing auth user table through Docker PostgreSQL..."
    Get-Content "docs\sql\init_auth_tables.sql" | docker compose exec -T $Service psql -U chaincloud -d chaincloud_memory_dev
}

Write-Host "Verifying tables..."
docker compose exec -T $Service psql -U chaincloud -d chaincloud_memory_dev -c "\dt"

Write-Host ""
Write-Host "Done."
Write-Host "Use this in .env:"
Write-Host "POSTGRES_HOST_PORT=15432"
Write-Host "MEMORY_STORE_BACKEND=postgres"
Write-Host "MEMORY_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev"
Write-Host "MEMORY_POSTGRES_TABLE=agent_memories"
Write-Host "MEMORY_POSTGRES_AUTO_CREATE=0"
