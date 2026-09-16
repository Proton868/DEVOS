"""Repository deploy assets must exist for a clean Ubuntu install."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repo_deploy_simulation_script():
    script = ROOT / "scripts" / "repo_deploy_simulation.py"
    assert script.is_file()
    # --skip-imports keeps this lightweight if heavy deps missing
    r = subprocess.run(
        [sys.executable, str(script), "--skip-imports"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "repo_deploy_simulation: OK" in r.stdout


def test_ops_scripts_executable_bits_or_present():
    for name in ("install.sh", "update.sh", "verify.sh", "apply_migrations.sh"):
        p = ROOT / "ops" / name
        assert p.is_file(), name
