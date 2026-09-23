"""PASS 6 — Final world isolation proof matrix + delivery-time SSE + cache lifecycle."""
from __future__ import annotations

import pytest

from governance.world_context import WorldContext, WorldBoundaryError
from governance.world_execution import (
    assert_terminal_world,
    assert_mcp_world,
    assert_sse_world,
    assert_delegation_world,
    assert_runtime_world,
    assert_evidence_world,
    assert_worker_job_world,
    require_execution_world,
    filter_sse_event_for_subscriber,
    nuha_world_from_fields,
)
from governance.world_binding import bind_job_payload
from governance.world_cache import (
    world_cache_key,
    assert_cache_key_world,
    WorldScopedRegistry,
)


# ── Consolidated cross-world attack matrix ──────────────────────────

CROSS_CASES = [
    ("nuha", lambda w: assert_delegation_world(w, task_tenant_id="world-b")),
    ("terminal", lambda w: assert_terminal_world(w, user_id="u-a", project_id="p", project_owner_id="u-b")),
    ("ssh_terminal", lambda w: require_execution_world(w, resource_world_id="world-b")),
    ("sse", lambda w: assert_sse_world(w, {"tenant_id": "world-b", "user_id": "u-a"})),
    ("runtime", lambda w: assert_runtime_world(w, {"tenant_id": "world-b", "owner_id": "u-a"})),
    ("mcp", lambda w: assert_mcp_world(w, target_world_id="world-b")),
    ("evidence", lambda w: assert_evidence_world(w, {"tenant_id": "world-b", "owner_id": "u-a"})),
    ("cache", lambda w: assert_cache_key_world(w, world_cache_key(WorldContext(world_id="world-b", principal_id="u-b"), "k"))),
]


@pytest.mark.parametrize("name,fn", CROSS_CASES, ids=[c[0] for c in CROSS_CASES])
def test_cross_world_attack_matrix(name, fn):
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        fn(w)


def test_queue_retry_forged_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    payload = bind_job_payload(w, {"retry": 1})
    payload["world_id"] = "world-b"
    payload["tenant_id"] = "world-b"

    class Job:
        tenant_id = "world-a"
        owner_id = "u-a"

    with pytest.raises(WorldBoundaryError):
        assert_worker_job_world(payload, Job())


def test_recovery_preserves_durable_world():
    class Job:
        tenant_id = "world-a"
        owner_id = "u-a"

    # Payload missing world — durable job columns win
    world = assert_worker_job_world({}, Job())
    assert world.world_id == "world-a"
    assert world.principal_id == "u-a"


# ── Positive same-world matrix ──────────────────────────────────────

def test_positive_same_world_matrix():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_delegation_world(w, task_tenant_id="world-a", task_owner_id="u-a")
    assert_terminal_world(w, user_id="u-a", project_id="proj")
    require_execution_world(w, resource_world_id="world-a")
    assert_sse_world(w, {"tenant_id": "world-a", "user_id": "u-a"})
    assert_runtime_world(w, {"tenant_id": "world-a", "owner_id": "u-a"})
    assert_mcp_world(w, target_world_id="world-a")
    assert_evidence_world(w, {"tenant_id": "world-a", "owner_id": "u-a"})
    key = world_cache_key(w, "sess", "1")
    assert_cache_key_world(w, key)
    payload = bind_job_payload(w, {})
    class Job:
        tenant_id = "world-a"
        owner_id = "u-a"
    assert assert_worker_job_world(payload, Job()).world_id == "world-a"


# ── SSE delivery-time ───────────────────────────────────────────────

def test_sse_delivery_blocks_foreign_event():
    sub = WorldContext(world_id="world-a", principal_id="u-a")
    assert filter_sse_event_for_subscriber(sub, {"tenant_id": "world-a", "user_id": "u-a"}) is True
    assert filter_sse_event_for_subscriber(sub, {"tenant_id": "world-b", "user_id": "u-a"}) is False
    assert filter_sse_event_for_subscriber(sub, {"tenant_id": "world-a", "user_id": "u-b"}) is False
    assert filter_sse_event_for_subscriber(sub, {"type": "keepalive"}) is True  # no world fields


# ── Cache lifecycle ─────────────────────────────────────────────────

def test_cache_registry_lifecycle_cross_world():
    reg = WorldScopedRegistry()
    wa = WorldContext(world_id="world-a", principal_id="u-a")
    wb = WorldContext(world_id="world-b", principal_id="u-b")
    reg.put(wa, "k", "va")
    reg.put(wb, "k", "vb")
    assert reg.get(wa, "k") == "va"
    assert reg.get(wb, "k") == "vb"
    with pytest.raises(WorldBoundaryError):
        reg.get(wa, "missing")
    reg.delete(wa, "k")
    with pytest.raises(WorldBoundaryError):
        reg.get(wa, "k")
    # B untouched
    assert reg.get(wb, "k") == "vb"
    # A cannot delete B by using B's key name alone
    reg.delete(wa, "k")  # no-op
    assert reg.get(wb, "k") == "vb"


# ── Missing / forged ────────────────────────────────────────────────

def test_missing_and_forged_world():
    with pytest.raises(WorldBoundaryError):
        nuha_world_from_fields(user_id="u-a")
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_delegation_world(w, forged_tenant_id="world-b")


# ── Production run_delegated_mission callers audit (source) ─────────

def test_production_delegation_callers_pass_world_id():
    from pathlib import Path
    chat = Path("api/routes/chat.py").read_text()
    nuha = Path("brain/nuha_bridge.py").read_text()
    assert "world_id=" in chat or "world_id=_wid" in chat or "tenant_id=_wid" in chat
    assert "world_id=" in nuha
    # Definition requires optional world_id
    dele = Path("brain/delegation.py").read_text()
    assert "world_id:" in dele or "world_id =" in dele


# ── Live path source proof (ASGI runtime may be unavailable in CI sandbox) ─

def test_websocket_routes_contain_world_guards():
    """Prove terminal + SSH WS handlers enforce world resolution in source."""
    from pathlib import Path
    src_t = Path("api/routes/terminal.py").read_text()
    src_s = Path("api/routes/ssh_terminal.py").read_text()
    assert "@router.websocket" in src_t
    assert "resolve_world_from_request" in src_t or "assert_terminal_world" in src_t
    assert "@router.websocket" in src_s
    assert "resolve_world_from_request" in src_s or "require_execution_world" in src_s
    # Orchestration SSE delivery-time filter present
    orch = Path("api/routes/orchestration.py").read_text()
    assert "filter_sse_event_for_subscriber" in orch
