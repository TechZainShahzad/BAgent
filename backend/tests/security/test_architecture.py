import subprocess
import sys


def test_import_linter_contracts_hold():
    r = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "--config", "pyproject.toml"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
