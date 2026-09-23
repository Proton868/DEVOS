"""PASS 4 — World execution boundary attack matrix."""
from __future__ import annotations

import pytest

from governance.world_context import WorldContext, WorldBoundaryError
from governance.world_execution import (
    require_execution_world,
    nuha_world_from_fields,
    assert_delegation_world,
    assert_terminal_world,
    assert_runtime_world,
    assert_mcp_world,
    assert_worker_job_world,
    assert_evidence_world,
    assert_sse_world,
)
from governance.world_binding import bind_job_payload


# ── Nuha / delegation ──────────────────────────────────────────────

def test_nuha_world_a_to_a_allow():
    w = nuha_world_from_fields(user_id="u-a", world_id="world-a")
    assert_delegation_world(w)
    assert w.world_id == "world-a"


def test_nuha_world_a_to_b_deny():
    w = nuha_world_from_fields(user_id="u-a", world_id="world-a")
    with pytest.raises(WorldBoundaryError) as ei:
        assert_delegation_world(w, task_tenant_id="world-b")
    assert ei.value.code == "CROSS_WORLD_DENIED"


def test_nuha_missing_world_deny():
    with pytest.raises(WorldBoundaryError) as ei:
        nuha_world_from_fields(user_id="u-a", world_id=None, tenant_id=None)
    assert ei.value.code == "WORLD_REQUIRED"


def test_nuha_forged_tenant_deny():
    w = nuha_world_from_fields(user_id="u-a", world_id="world-a")
    with pytest.raises(WorldBoundaryError):
        assert_delegation_world(w, forged_tenant_id="world-b")


def test_nuha_wrong_principal_deny():
    w = nuha_world_from_fields(user_id="u-a", world_id="world-a")
    with pytest.raises(WorldBoundaryError):
        assert_delegation_world(w, task_owner_id="u-b")


# ── Terminal ───────────────────────────────────────────────────────

def test_terminal_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_terminal_world(w, user_id="u-a", project_id="proj1")


def test_terminal_cross_principal_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-b", project_id="proj1")


def test_terminal_path_traversal_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-a", project_id="../world-b")


def test_terminal_foreign_project_owner_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-a", project_id="p", project_owner_id="u-b")


# ── Runtime ─────────────────────────────────────────────────────────

def test_runtime_cross_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_runtime_world(w, {"tenant_id": "world-b", "owner_id": "u-a", "runtime_id": "r1"})


def test_runtime_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_runtime_world(w, {"tenant_id": "world-a", "owner_id": "u-a", "runtime_id": "r1"})


# ── MCP ─────────────────────────────────────────────────────────────

def test_mcp_target_other_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_mcp_world(w, target_world_id="world-b")


def test_mcp_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_mcp_world(w, target_world_id="world-a")


# ── Queue / worker ──────────────────────────────────────────────────

def test_worker_job_payload_world_ok():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    pl = bind_job_payload(w, {"action": "build"})

    class Job:
        tenant_id = "world-a"
        owner_id = "u-a"
        payload = pl

    got = assert_worker_job_world(pl, Job())
    assert got.world_id == "world-a"


def test_worker_job_payload_forged_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    payload = bind_job_payload(w, {})
    payload["world_id"] = "world-b"  # mutate after bind
    payload["tenant_id"] = "world-b"

    class Job:
        tenant_id = "world-a"
        owner_id = "u-a"

    with pytest.raises(WorldBoundaryError):
        assert_worker_job_world(payload, Job())


def test_worker_job_missing_world_deny():
    with pytest.raises(WorldBoundaryError):
        assert_worker_job_world({}, None)


def test_worker_job_principal_mismatch_deny():
    class Job:
        tenant_id = "world-a"
        owner_id = "u-a"

    payload = {"world_id": "world-a", "tenant_id": "world-a", "owner_id": "u-b", "principal_id": "u-b"}
    with pytest.raises(WorldBoundaryError):
        assert_worker_job_world(payload, Job())


# ── Evidence ────────────────────────────────────────────────────────

def test_evidence_cross_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_evidence_world(w, {"tenant_id": "world-b", "owner_id": "u-a"})


def test_evidence_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_evidence_world(w, {"tenant_id": "world-a", "owner_id": "u-a"})


# ── SSE ─────────────────────────────────────────────────────────────

def test_sse_cross_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_sse_world(w, {"tenant_id": "world-b", "user_id": "u-a"})


def test_sse_wrong_principal_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_sse_world(w, {"tenant_id": "world-a", "user_id": "u-b"})


def test_sse_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_sse_world(w, {"tenant_id": "world-a", "user_id": "u-a"})


# ── require_execution_world ─────────────────────────────────────────

def test_require_execution_world_mismatch():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        require_execution_world(w, resource_world_id="world-b")


def test_require_execution_world_ok():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert require_execution_world(w, resource_world_id="world-a").world_id == "world-a"


# ── Indirect matrix (documented as unit-level chains) ───────────────

@pytest.mark.parametrize("attacker,target", [
    ("world-a", "world-b"),
])
def test_indirect_chains_deny(attacker, target):
    w = WorldContext(world_id=attacker, principal_id="u-a")
    # A → Nuha → B
    with pytest.raises(WorldBoundaryError):
        assert_delegation_world(w, task_tenant_id=target)
    # A → Terminal → B project owner
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-a", project_id="p", project_owner_id="u-b")
    # A → Runtime → B
    with pytest.raises(WorldBoundaryError):
        assert_runtime_world(w, {"tenant_id": target, "owner_id": "u-a"})
    # A → MCP → B
    with pytest.raises(WorldBoundaryError):
        assert_mcp_world(w, target_world_id=target)
    # A → Evidence → B
    with pytest.raises(WorldBoundaryError):
        assert_evidence_world(w, {"tenant_id": target, "owner_id": "u-a"})
    # A → SSE → B
    with pytest.raises(WorldBoundaryError):
        assert_sse_world(w, {"tenant_id": target, "user_id": "u-a"})
    # A → Queue → B
    payload = bind_job_payload(w, {})
    payload["world_id"] = target
    payload["tenant_id"] = target
    class Job:
        tenant_id = attacker
        owner_id = "u-a"
    with pytest.raises(WorldBoundaryError):
        assert_worker_job_world(payload, Job())
