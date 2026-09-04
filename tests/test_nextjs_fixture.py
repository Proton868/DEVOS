"""Live-ish Next.js fixture: requires prior npm install+build in tests/fixtures/nextjs-delivery."""
import os
import subprocess
import time
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures" / "nextjs-delivery"


@pytest.mark.skipif(not (FIX / "node_modules").exists(), reason="fixture not installed")
def test_nextjs_build_artifacts_exist():
    assert (FIX / ".next").exists() or True  # may need rebuild
    # rebuild quickly if .next missing
    if not (FIX / ".next").exists():
        r = subprocess.run(["npm", "run", "build"], cwd=FIX, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-1000:]
    assert (FIX / ".next").exists()


@pytest.mark.skipif(not (FIX / "node_modules").exists(), reason="fixture not installed")
def test_nextjs_start_health():
    if not (FIX / ".next").exists():
        subprocess.run(["npm", "run", "build"], cwd=FIX, check=True, timeout=180)
    proc = subprocess.Popen(
        ["npm", "run", "start"],
        cwd=FIX,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        import urllib.request
        body = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen("http://127.0.0.1:3912/", timeout=2) as r:
                    body = r.read().decode()
                    break
            except Exception:
                continue
        assert body is not None, "no HTTP response"
        assert "DevOS Delivery Fixture" in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
