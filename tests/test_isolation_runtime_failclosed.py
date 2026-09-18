"""isolation_runtime must not fall back to bare process for untrusted."""
from __future__ import annotations

import os
import pytest


def test_untrusted_process_mode_raises(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.delenv("DEVOS_ALLOW_DEGRADED_ISOLATION", raising=False)
    from execution.isolation_runtime import wrap_command, IsolationUnavailable, isolation_available

    info = isolation_available()
    assert info["mode"] == "process"
    assert info["suitable_for_untrusted"] is False
    with pytest.raises(IsolationUnavailable):
        wrap_command(["echo", "hi"], cwd="/tmp", trust="untrusted")


def test_trusted_may_use_process(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    from execution.isolation_runtime import wrap_command

    out = wrap_command(["echo", "hi"], cwd="/tmp", trust="trusted")
    assert out == ["echo", "hi"]


def test_isolation_available_shape():
    from execution.isolation_runtime import isolation_available

    info = isolation_available()
    assert "mode" in info
    assert "suitable_for_untrusted" in info
    assert "enforced" in info
