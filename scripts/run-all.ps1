# Start the whole product: database, migrations, API, worker, dashboard (each in its own window).
# Works from ANY folder and whether or not the virtual environment is activated.
#   D:\BaAi\scripts\run-all.ps1                 -> safe default (AUTONOMY_MODE from .env, normally dry_run)
#   D:\BaAi\scripts\run-all.ps1 -Autonomous     -> the worker may create Jira issues without approval
#   D:\BaAi\scripts\run-all.ps1 -Mode propose   -> actions wait for approval in the dashboard
param(
  [switch]$Autonomous,
  [ValidateSet("", "dry_run", "propose", "autonomous")][string]$Mode = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if ($Autonomous) { $Mode = "autonomous" }

# the project's own interpreter: no dependency on PATH, `uv`, or an activated venv
$py = "$root\backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "No virtual environment yet: creating it (one-time)..." -ForegroundColor Yellow
  Set-Location "$root\backend"
  python -m uv sync --python 3.12 --extra capture
  Set-Location $root
  if (-not (Test-Path $py)) { throw "Could not create backend\.venv. Run: cd backend; python -m uv sync --extra capture" }
}

Write-Host "1/4 database..." -ForegroundColor Cyan
# docker prints progress on stderr; that is not a failure
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
docker info 2>&1 | Out-Null
if ($LASTEXITCODE) {
  $dockerExe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
  if (-not (Test-Path $dockerExe)) { throw "Docker is not running and Docker Desktop was not found at $dockerExe." }
  Write-Host "    Docker Desktop is not running: starting it..." -ForegroundColor Yellow
  Start-Process $dockerExe
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep 3
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
  }
  if ($LASTEXITCODE) { $ErrorActionPreference = $prev; throw "Docker Desktop did not start within 3 minutes. Open it manually and retry." }
}
docker compose up -d db 2>&1 | Out-Null
$ErrorActionPreference = $prev
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  $ErrorActionPreference = "Continue"
  docker compose exec -T db pg_isready -U ba -d ba 2>&1 | Out-Null
  $ErrorActionPreference = $prev
  if ($LASTEXITCODE -eq 0) { $ready = $true; break }
  Start-Sleep 2
}
if (-not $ready) { throw "Postgres did not become ready. Is Docker Desktop running?" }

Write-Host "2/4 migrations..." -ForegroundColor Cyan
Set-Location "$root\backend"
& $py -m alembic upgrade head
if ($LASTEXITCODE) { throw "Database migration failed (see the message above)." }

$modeEnv = if ($Mode) { "`$env:AUTONOMY_MODE='$Mode'; " } else { "" }
Write-Host "3/4 API + worker (new windows)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; $modeEnv & '$py' -m uvicorn --factory app.api.main:create_app --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; $modeEnv & '$root\backend\.venv\Scripts\ba.exe' worker"

Write-Host "4/4 dashboard (new window)..." -ForegroundColor Cyan
if (-not (Test-Path "$root\frontend\node_modules")) { Set-Location "$root\frontend"; npm install }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"

Write-Host ""
Write-Host "Dashboard : http://127.0.0.1:5173   (API: http://127.0.0.1:8000/health)" -ForegroundColor Green
Write-Host "Mode      : $(if ($Mode) { $Mode } else { '.env AUTONOMY_MODE (default dry_run)' })" -ForegroundColor Green
Write-Host "Stop all  : $root\scripts\stop-all.ps1"
