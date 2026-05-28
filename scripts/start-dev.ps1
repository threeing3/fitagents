param(
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "logs\experiments"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "$timestamp-start-dev.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    $line | Tee-Object -FilePath $logPath -Append
}

Write-Log "Starting AI Fitness Coach Agent development stack."
Write-Log "Repository: $repoRoot"

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Log "Created .env from .env.example. Fill the selected provider API key for live model calls."
}

Write-Log "Validating Docker Compose configuration."
docker compose config --quiet

Write-Log "Building and starting Docker Compose services."
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$composeOutput = & cmd.exe /d /c "docker compose up -d --build 2>&1"
$composeExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
$composeOutput | Tee-Object -FilePath $logPath -Append

if ($composeExitCode -ne 0) {
    throw "Docker Compose startup failed with exit code $composeExitCode."
}

Write-Log "Waiting for FastAPI health endpoint."
$healthy = $false
for ($i = 1; $i -le 40; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:1015/health" -TimeoutSec 5
        Write-Log "FastAPI health: $($health | ConvertTo-Json -Compress)"
        $healthy = $true
        break
    } catch {
        Write-Log "Health check attempt $i/40 failed: $($_.Exception.Message)"
        Start-Sleep -Seconds 3
    }
}

if (-not $healthy) {
    Write-Log "FastAPI did not become healthy. Showing recent backend logs."
    docker compose logs --tail 120 fast_api_ai_fitness_planner 2>&1 | Tee-Object -FilePath $logPath -Append
    throw "FastAPI health check failed."
}

if (-not $SkipSmokeTest) {
    Write-Log "Running smoke test."
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "smoke-test.ps1") 2>&1 |
        Tee-Object -FilePath $logPath -Append
}

Write-Log "Development stack is ready."
Write-Log "Web UI: http://localhost:5173"
Write-Log "API docs: http://localhost:1015/docs"
