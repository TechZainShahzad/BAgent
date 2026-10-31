# Stop the API, worker, dashboard and any capture process started from this repo.
# The database container keeps running (your data is safe); stop it with: docker compose stop db
$patterns = @('uvicorn.*create_app', 'ba worker', 'ba\.exe.*worker', 'ba capture', 'ba\.exe.*capture', 'vite', 'npm run dev')
$me = $PID
foreach ($proc in Get-CimInstance Win32_Process) {
  if (-not $proc.CommandLine -or $proc.ProcessId -eq $me) { continue }
  # only the product's own process types, never shells or editors that merely mention these words
  if ($proc.Name -notmatch '^(python|pythonw|uv|ba|uvicorn|node|cmd)(\.exe)?$') { continue }
  foreach ($p in $patterns) {
    if ($proc.CommandLine -match $p -and $proc.CommandLine -notmatch 'stop-all') {
      try {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        $c = $proc.CommandLine
        Write-Host ("stopped {0}: {1}" -f $proc.ProcessId, $c.Substring(0, [Math]::Min(80, $c.Length)))
      } catch {}
      break
    }
  }
}
Write-Host "done"
