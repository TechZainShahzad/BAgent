# Full verification: lint, types, tests (backend + frontend + build). Requires `.\scripts\dev.ps1 up` and `migrate` first.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = "$root\backend\.venv\Scripts\python.exe"
Set-Location "$root\backend"
$env:ALEMBIC_DATABASE_URL = "postgresql+psycopg://ba:ba_dev_password@127.0.0.1:5433/ba_test"

& $py -m ruff check .;           if ($LASTEXITCODE) { exit 1 }
& $py -m ruff format --check .;  if ($LASTEXITCODE) { exit 1 }
& $py -m mypy app;               if ($LASTEXITCODE) { exit 1 }
& $py -m pytest -q;              if ($LASTEXITCODE) { exit 1 }

if (Test-Path "$root\frontend\package.json") {
  Set-Location "$root\frontend"
  npm test --silent;      if ($LASTEXITCODE) { exit 1 }
  npm run build --silent; if ($LASTEXITCODE) { exit 1 }
}
Write-Host "ALL CHECKS PASSED"
