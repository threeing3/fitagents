# AI Fitness Coach — Frontend Dev Server Launcher
# Double-click this file in File Explorer, or run in PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start-web.ps1

$ErrorActionPreference = "Stop"
$webDir = "C:\Users\Lenovo\Documents\New project 4\ai-fitness-planner\web"

Write-Host "========================================" -ForegroundColor Green
Write-Host " AI Fitness Coach - Frontend Dev Server" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Check Node.js
try {
    $nodeVersion = node --version 2>$null
    Write-Host "[OK] Node.js $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Node.js is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Download from https://nodejs.org (LTS recommended)" -ForegroundColor Yellow
    pause
    exit 1
}

# Install dependencies
Set-Location $webDir
if (-not (Test-Path "node_modules")) {
    Write-Host "[...] Installing dependencies (npm install)..." -ForegroundColor Yellow
    npm install
    Write-Host "[OK] Dependencies installed." -ForegroundColor Green
} else {
    Write-Host "[OK] node_modules exists, skipping install." -ForegroundColor Green
}

# Build check
Write-Host "[...] Running TypeScript check (vite build)..." -ForegroundColor Yellow
$buildResult = npx vite build 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Build had issues:" -ForegroundColor Yellow
    Write-Host $buildResult
    Write-Host ""
    Write-Host "Attempting dev server anyway..." -ForegroundColor Yellow
} else {
    Write-Host "[OK] Build succeeded." -ForegroundColor Green
}

Write-Host ""
Write-Host "Starting dev server on http://localhost:5173" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Cyan
Write-Host ""

npx vite --host
