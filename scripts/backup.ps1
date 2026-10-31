# Backup / restore drill for the BA platform database.
#   .\scripts\backup.ps1 backup          -> backups\ba-<timestamp>.dump
#   .\scripts\backup.ps1 drill [file]    -> restore into scratch DB ba_restore_drill, verify, drop it
param([string]$Task = "backup", [string]$File = "")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = "$root\backend\.venv\Scripts\python.exe"
Set-Location $root
$c = "baai-db-1"

function Psql([string]$db, [string]$sql) {
  docker exec $c psql -U ba -d $db -tA -c $sql
}

switch ($Task) {
  "backup" {
    New-Item -ItemType Directory -Force backups | Out-Null
    $name = "ba-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".dump"
    docker exec $c pg_dump -U ba -d ba -Fc -f "/tmp/$name"
    docker cp "${c}:/tmp/$name" "backups\$name"
    docker exec $c rm "/tmp/$name"
    Write-Host "backup written: backups\$name ($((Get-Item "backups\$name").Length) bytes)"
  }
  "drill" {
    if (-not $File) { $File = (Get-ChildItem backups\*.dump | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName }
    $leaf = Split-Path $File -Leaf
    docker cp $File "${c}:/tmp/$leaf"
    Psql postgres "DROP DATABASE IF EXISTS ba_restore_drill" | Out-Null
    Psql postgres "CREATE DATABASE ba_restore_drill" | Out-Null
    docker exec $c pg_restore -U ba -d ba_restore_drill --no-owner "/tmp/$leaf"
    $tables = "clients","meetings","requirements","evidence","action_intents","audit_events","jobs"
    $bad = 0
    foreach ($t in $tables) {
      $a = Psql ba "SELECT count(*) FROM $t"
      $b = Psql ba_restore_drill "SELECT count(*) FROM $t"
      $ok = if ($a -eq $b) { "OK " } else { $bad++; "DIFF" }
      Write-Host ("{0} {1,-16} live={2} restored={3}" -f $ok, $t, $a, $b)
    }
    $chainsLive = Psql ba "SELECT count(DISTINCT chain_key) FROM audit_events"
    $chainsRest = Psql ba_restore_drill "SELECT count(DISTINCT chain_key) FROM audit_events"
    Write-Host "audit chains live=$chainsLive restored=$chainsRest"
    # re-verify every hash chain in the restored copy with the application's own verifier
    $env:DATABASE_URL = "postgresql+psycopg://ba:ba_dev_password@127.0.0.1:5433/ba_restore_drill"
    Set-Location "$root\backend"
    & $py -c "from app.persistence.db import unit_of_work; from app.orchestration.audit import verify_client_chain; from app.persistence.models import AuditEvent; import uuid; from sqlalchemy import select;
with unit_of_work() as s:
    keys = s.execute(select(AuditEvent.chain_key).distinct()).scalars().all()
    broken = [k for k in keys if verify_client_chain(s, None if k == 'system' else uuid.UUID(k)) is not None]
print('restored chains verified:', len(keys) - len(broken), 'broken:', broken)
import sys; sys.exit(1 if broken else 0)"
    if ($LASTEXITCODE) { $bad++ }
    Set-Location $root
    docker exec $c rm "/tmp/$leaf"
    Psql postgres "DROP DATABASE ba_restore_drill" | Out-Null
    if ($bad) { Write-Error "restore drill FAILED ($bad problem(s))"; exit 1 }
    Write-Host "RESTORE DRILL PASSED"
  }
  default { Write-Error "unknown task $Task" }
}
