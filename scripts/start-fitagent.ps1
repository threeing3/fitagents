param([switch]$Offline, [switch]$CheckOnly, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoPath
$pythonPath = Join-Path $repoPath '.venv\Scripts\python.exe'
$pgCtlPath = 'E:\PostgreSQL\bin\pg_ctl.exe'
$dataPath = Join-Path $repoPath 'logs\postgres_isolated_20261002'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Project virtual environment not found.' }
if (-not (Test-Path -LiteralPath $pgCtlPath)) { throw 'Existing PostgreSQL executable not found.' }
if (-not (Test-Path -LiteralPath (Join-Path $dataPath 'PG_VERSION'))) { throw 'Existing preview database not found; no database will be created.' }
if (-not (Test-Path -LiteralPath 'web\dist\index.html')) { throw 'Build frontend first: cd web; npm run build' }
$listener = Get-NetTCPConnection -State Listen -LocalPort 8015 -ErrorAction SilentlyContinue
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($process.CommandLine -notmatch 'run_local_preview|fitagent_acceptance_20261002') {
        throw 'Port 8015 belongs to an unrecognized process. It will not be stopped.'
    }
    $health = Invoke-RestMethod 'http://127.0.0.1:8015/health/ready' -TimeoutSec 5
    if ($health.status -ne 'ready') { throw 'Existing app is not ready; inspect its logs.' }
    Write-Host 'FitAgent is already running: http://127.0.0.1:8015/'
    if (-not $NoBrowser -and -not $CheckOnly) { Start-Process 'http://127.0.0.1:8015/' }
    return
}
$databaseListener = Get-NetTCPConnection -State Listen -LocalPort 15432 -ErrorAction SilentlyContinue
if (-not $databaseListener) {
    & $pgCtlPath -D $dataPath -l (Join-Path $repoPath 'logs\postgres_oneclick.log') -o '-h 127.0.0.1 -p 15432' -w -t 15 start
    if ($LASTEXITCODE -ne 0) { throw 'Preview database could not start; data was not reset.' }
}
$arguments = @('-m', 'scripts.run_local_preview')
if ($Offline) { $arguments += '--offline' }
if ($CheckOnly) { $arguments += '--check-only' }
Write-Host 'Using the existing local preview database; no migration or background worker.'
if (-not $Offline) { Write-Host 'Existing model credentials will be used. Requests can incur charges.' }
# The browser may open before startup is complete; refresh after the terminal reports ready.
if (-not $NoBrowser -and -not $CheckOnly) { Start-Process 'http://127.0.0.1:8015/' }
& $pythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Application stopped with an error; inspect the terminal output.' }
