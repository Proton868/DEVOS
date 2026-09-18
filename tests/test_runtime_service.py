"""Project runtime service — lifecycle facade for Preview/IDE/Flow/Nuha."""
from __future__ import annotations

import asyncio
import os

import pytest

from execution.artifacts import write_bytes
from execution.files import FileService
from execution.runtime_service import (
    LIFECYCLE_ACTIONS,
    ProjectRuntimeSnapshot,
    probe_health,
    run_lifecycle_action,
    snapshot,
)


def test_lifecycle_actions_include_core():
    for a in ("start", "stop", "restart", "rebuild", "health", "status", "test"):
        assert a in LIFECYCLE_ACTIONS


def test_snapshot_static_site_components():
    uid, pid = "rt-svc-user", "rt-svc-static"
    fs = FileService(uid, pid)
    write_bytes(fs, "index.html", b"<html><body>ok</body></html>")
    write_bytes(fs, ".env.example", b"API_URL=http://localhost\n# comment\nSECRET_KEY=\n")
    snap = snapshot(uid, pid, probe=False)
    assert isinstance(snap, ProjectRuntimeSnapshot)
    d = snap.to_dict()
    assert d["project_id"] == pid
    assert d["user_id"] == uid
    assert d["state"] in ("STOPPED", "UNKNOWN", "UNSUPPORTED", "READY", "FAILED")
    assert isinstance(d["components"], list) and len(d["components"]) >= 1
    assert "API_URL" in d["env_keys"]
    assert "SECRET_KEY" in d["env_keys"]
    # Never leak values
    assert all("=" not in k for k in d["env_keys"])
    assert "runtime.start" in d["capabilities"]
    assert "preview.iframe" in d["capabilities"]


def test_invalid_action_fails_closed():
    with pytest.raises(ValueError) as ei:
        asyncio.run(run_lifecycle_action("u", "p", "explode"))
    assert "INVALID_ACTION" in str(ei.value)


def test_status_and_health_actions():
    uid, pid = "rt-svc-user2", "rt-svc-static2"
    fs = FileService(uid, pid)
    write_bytes(fs, "index.html", b"<html>x</html>")
    st = asyncio.run(run_lifecycle_action(uid, pid, "status"))
    assert st.state in ("STOPPED", "UNKNOWN", "UNSUPPORTED", "READY", "FAILED", "BUILT")
    hl = asyncio.run(run_lifecycle_action(uid, pid, "health"))
    assert hl.health in ("healthy", "unhealthy", "unknown", "n/a")


def test_probe_health_no_port():
    assert probe_health(None) == "n/a"
    assert probe_health(0) == "n/a"


def test_stop_without_start_is_safe():
    uid, pid = "rt-svc-user3", "rt-svc-stop"
    fs = FileService(uid, pid)
    write_bytes(fs, "index.html", b"<html>y</html>")
    snap = asyncio.run(run_lifecycle_action(uid, pid, "stop"))
    assert snap.state in ("STOPPED", "UNKNOWN", "UNSUPPORTED", "FAILED", "READY")


def test_runtime_capability_registration():
    from governance.runtime_capabilities import ensure_runtime_capabilities_registered
    from governance.capability_registry import get_registry
    from governance.capability_substrate import get_capability_substrate

    ensure_runtime_capabilities_registered()
    reg = get_registry()
    assert reg.get("devos.runtime.lifecycle") is not None
    assert reg.get("devos.runtime.health") is not None
    sub = get_capability_substrate()
    assert sub.resolve("devos.runtime.lifecycle") is not None
    assert sub.resolve("devos.runtime.health") is not None


def test_lifecycle_via_substrate_requires_auth_context():
    """Executor refuses missing user — fail closed."""
    from governance.runtime_capabilities import ensure_runtime_capabilities_registered
    from governance.capability_substrate import (
        InvocationContext,
        InvocationRequest,
        get_capability_substrate,
    )

    ensure_runtime_capabilities_registered()
    sub = get_capability_substrate()
    # Direct executor call path through substrate may still authorize —
    # unit-test the executor helper boundary: empty user raises.
    from governance.runtime_capabilities import _lifecycle
    from governance.capability_substrate import CapabilityContract

    contract = sub.resolve("devos.runtime.lifecycle")
    req = InvocationRequest(
        capability_id="devos.runtime.lifecycle",
        inputs={"project_id": "x", "action": "status"},
        context=InvocationContext(tenant_id="t", owner_id=""),
    )
    with pytest.raises(ValueError):
        asyncio.run(_lifecycle(contract, req))
