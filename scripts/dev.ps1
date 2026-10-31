# Dev helper. Usage: .\scripts\dev.ps1 [up|migrate|api|worker|reset-test-db]
param([string]$Task = "up")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = "$root\backend\.venv\Scripts\python.exe"
Set-Location $root

switch ($Task) {
  "up" {
    docker compose up -d db
    docker compose exec -T db pg_isready -U ba -d ba
    docker compose exec -T db psql -U ba -d ba -tc "SELECT 1 FROM pg_database WHERE datname='ba_test'" | Select-String 1 | Out-Null
    if (-not $?) { docker compose exec -T db psql -U ba -d ba -c "CREATE DATABASE ba_test" }
  }
  "migrate" {
    Set-Location "$root\backend"
    & $py -m alembic upgrade head
    $env:ALEMBIC_DATABASE_URL = "postgresql+psycopg://ba:ba_dev_password@127.0.0.1:5433/ba_test"
    & $py -m alembic upgrade head
  }
  "api" { Set-Location "$root\backend"; & $py -m uvicorn --factory app.api.main:create_app --reload --host 127.0.0.1 }
  "worker" { Set-Location "$root\backend"; & "$root\backend\.venv\Scripts\ba.exe" worker }
  default { Write-Error "unknown task $Task" }
}
