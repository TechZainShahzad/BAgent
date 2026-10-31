# Create / refresh a READ-ONLY database login for demos and presentations.
#   .\scripts\db-viewer.ps1            -> role ba_viewer / password viewer_demo (SELECT only)
# It can read everything EXCEPT the users table (password hashes), and can never change data.
# Re-run it after new migrations so the viewer can see new tables.
$ErrorActionPreference = "Stop"
$sql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ba_viewer') THEN
    CREATE ROLE ba_viewer LOGIN PASSWORD 'viewer_demo';
  END IF;
END `$`$;
ALTER ROLE ba_viewer SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE ba TO ba_viewer;
GRANT USAGE ON SCHEMA public TO ba_viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ba_viewer;
REVOKE ALL ON TABLE users FROM ba_viewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ba_viewer;
"@
$sql | docker exec -i baai-db-1 psql -U ba -d ba -v ON_ERROR_STOP=1 | Out-Null
Write-Host "read-only login ready: user ba_viewer / password viewer_demo (database ba, host 127.0.0.1, port 5433)"
