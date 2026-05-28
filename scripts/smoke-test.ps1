param(
    [string]$ApiBaseUrl = "http://localhost:1015"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "logs\experiments"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "$timestamp-smoke-test.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    $line | Tee-Object -FilePath $logPath -Append
}

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Path,
        [object]$Body = $null
    )

    $uri = "$ApiBaseUrl$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec 60
    }

    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $uri -ContentType "application/json" -Body $json -TimeoutSec 120
}

Write-Log "AI Fitness Coach Agent smoke test started."
Write-Log "API base URL: $ApiBaseUrl"

$health = Invoke-Json -Method "GET" -Path "/health"
Write-Log "Health response: $($health | ConvertTo-Json -Compress)"

if (-not $health.embedding_provider -or -not $health.embedding_model) {
    throw "Health response did not include embedding provider/model."
}

$session = Invoke-Json -Method "POST" -Path "/v1/chat/sessions" -Body @{
    display_name = "Smoke Test User"
    title = "Smoke Test Session"
}
Write-Log "Created session: $($session | ConvertTo-Json -Compress)"

$message = Invoke-Json -Method "POST" -Path "/v1/chat/messages" -Body @{
    session_id = $session.session_id
    user_id = $session.user_id
    message = "age 28, height 175cm, weight 72kg, goal muscle gain, beginner, dumbbells and barbell available. I prefer short evening workouts."
}
Write-Log "Chat response: $($message | ConvertTo-Json -Depth 20 -Compress)"

if (-not $message.agent_run_id) {
    throw "Chat response did not include agent_run_id."
}

$plan = Invoke-Json -Method "POST" -Path "/v1/plans/generate" -Body @{
    user_id = $session.user_id
    force = $true
    plan_days = 7
}
Write-Log "Plan response: $($plan | ConvertTo-Json -Depth 20 -Compress)"

if (-not $plan.plan.training_days -or $plan.plan.training_days.Count -lt 1) {
    throw "Generated plan does not include training days."
}

$checkin = Invoke-Json -Method "POST" -Path "/v1/checkins/daily" -Body @{
    user_id = $session.user_id
    sleep_hours = 5.5
    fatigue = 8
    soreness = 8
    stress = 6
    mood = "tired"
    nutrition_adherence = 70
    workout_completion = 55
    notes = "Poor sleep and high soreness after yesterday's workout."
}
Write-Log "Check-in response: $($checkin | ConvertTo-Json -Compress)"

$adjustedPlan = Invoke-Json -Method "POST" -Path "/v1/plans/adjust" -Body @{
    user_id = $session.user_id
    reason = "Smoke test verifies explicit adjustment after high fatigue check-in."
}
Write-Log "Explicit adjustment response: $($adjustedPlan | ConvertTo-Json -Depth 20 -Compress)"

if (-not $adjustedPlan.plan.training_days -or $adjustedPlan.rationale -notmatch "Adjusted plan") {
    throw "Explicit adjustment did not return an adjusted training plan."
}

$dashboard = Invoke-Json -Method "GET" -Path "/v1/users/$($session.user_id)/dashboard"
Write-Log "Dashboard response: $($dashboard | ConvertTo-Json -Depth 20 -Compress)"

if (-not $dashboard.progress.active_plan) {
    throw "Dashboard does not report an active plan."
}

$eval = Invoke-Json -Method "POST" -Path "/v1/evals/run" -Body @{
    suite_name = "smoke-test"
    persist_cases = $true
}
Write-Log "Eval response: $($eval | ConvertTo-Json -Depth 20 -Compress)"

if ($eval.passed -lt 5) {
    throw "Eval suite did not fully pass."
}

$run = Invoke-Json -Method "GET" -Path "/v1/agent-runs/$($message.agent_run_id)"
Write-Log "Agent run response: $($run | ConvertTo-Json -Depth 20 -Compress)"

if (-not $run.nodes -or $run.nodes.Count -lt 1) {
    throw "Agent run trace did not include graph nodes."
}

$nodeNames = @($run.nodes | ForEach-Object { $_.node })
foreach ($requiredNode in @("ProfileExtractorAgent", "MemoryAgent", "IntentRouter")) {
    if ($nodeNames -notcontains $requiredNode) {
        throw "Agent run trace did not include required node: $requiredNode"
    }
}

if (-not $run.log_path) {
    throw "Agent run did not include a readable log_path."
}

$expectedLogPath = Join-Path $repoRoot $run.log_path
if (-not (Test-Path $expectedLogPath)) {
    throw "Agent readable log does not exist: $expectedLogPath"
}

Write-Log "Smoke test completed successfully."
Write-Output "Smoke test passed. Log: $logPath"
