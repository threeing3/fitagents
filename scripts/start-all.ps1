# AI Fitness Coach — Full Stack Dev Launcher
# Starts: PostgreSQL + FastAPI (Docker) + Frontend (native Vite HMR)
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-all.ps1

param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$repoRoot = "C:\Users\Lenovo\Documents\New project 4\ai-fitness-planner"
Set-Location $repoRoot

$webDir = Join-Path $repoRoot "web"
$logDir = Join-Path $repoRoot "logs\experiments"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "$timestamp-start-all.log"

function Write-Step { param([string]$M, [string]$C="White") Write-Host "  $M" -ForegroundColor $C }
function Write-Ok { Write-Step "✓ $args" "Green" }
function Write-Info { Write-Step "$args" "Cyan" }
function Write-Warn { Write-Step "$args" "Yellow" }

Write-Host "" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  AI Fitness Coach — Full Stack Launch" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# ---- 1. Docker Backend ----
if (-not $SkipBackend) {
    Write-Info "Starting Docker services (postgres + fastapi)..."

    if (-not (Test-Path ".env")) {
        if (Test-Path ".env.example") {
            Copy-Item ".env.example" ".env"
            Write-Ok "Created .env from .env.example"
        } else {
            Write-Warn ".env not found — Docker may not start correctly"
        }
    }

    Write-Info "Validating docker compose config..."
    docker compose config --quiet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "docker compose config check returned non-zero — continuing anyway"
    }

    Write-Info "Building and starting Docker Compose..."
    docker compose up -d --build postgres fast_api_ai_fitness_planner 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ✗ Docker Compose failed" -ForegroundColor Red
        exit 1
    }
    Write-Ok "Docker services started"

    # Wait for health
    Write-Info "Waiting for FastAPI health check..."
    $healthy = $false
    for ($i = 1; $i -le 30; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:1015/health" -TimeoutSec 3
            Write-Ok "FastAPI healthy: provider=$($health.provider), model=$($health.chat_model)"
            $healthy = $true
            break
        } catch {
            Write-Host "    attempt $i/30..." -ForegroundColor DarkGray
            Start-Sleep -Seconds 2
        }
    }
    if (-not $healthy) {
        Write-Host "  ✗ FastAPI did not become healthy. Check logs:" -ForegroundColor Red
        docker compose logs --tail 30 fast_api_ai_fitness_planner
        exit 1
    }
} else {
    Write-Info "Skipping Docker backend (--SkipBackend)"
}

# ---- 2. Frontend Dev Server ----
if (-not $SkipFrontend) {
    Write-Info "Installing frontend dependencies..."
    Set-Location $webDir

    if (-not (Test-Path "node_modules")) {
        npm install 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ✗ npm install failed" -ForegroundColor Red
            exit 1
        }
        Write-Ok "Dependencies installed"
    } else {
        Write-Ok "node_modules exists, skipping install"
    }

    Write-Info "Starting Vite dev server on http://localhost:5173 ..."
    Write-Host ""
    Write-Host "  Backend API:  http://localhost:1015/docs" -ForegroundColor Cyan
    Write-Host "  Frontend UI:  http://localhost:5173" -ForegroundColor Cyan
    Write-Host "  Press Ctrl+C to stop the frontend server." -ForegroundColor DarkGray
    Write-Host ""

    npx vite --host 2>&1

} else {
    Write-Info "Skipping frontend (--SkipFrontend)"
    Write-Host ""
    Write-Host "  Backend API:  http://localhost:1015/docs" -ForegroundColor Cyan
    Write-Host ""
}
