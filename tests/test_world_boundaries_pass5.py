"""PASS 5 — Terminal WS, SSE, MCP, cache, remaining boundary coverage."""
from __future__ import annotations

import pytest

from governance.world_context import WorldContext, WorldBoundaryError
from governance.world_execution import (
    assert_terminal_world,
    assert_mcp_world,
    assert_sse_world,
    assert_delegation_world,
    nuha_world_from_fields,
)
from governance.world_cache import (
    world_cache_key,
    assert_cache_key_world,
    WorldScopedRegistry,
    parse_world_from_cache_key,
)


def test_terminal_ws_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_terminal_world(w, user_id="u-a", project_id="myproj")


def test_terminal_ws_cross_project_owner_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-a", project_id="p", project_owner_id="u-b")


def test_terminal_ws_traversal_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-a", project_id="../../etc")


def test_terminal_ws_wrong_principal_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-b", project_id="p")


def test_mcp_forged_target_in_args_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_mcp_world(w, target_world_id="world-b")


def test_mcp_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_mcp_world(w, target_world_id="world-a")


def test_sse_orchestration_plan_cross_world_deny():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    with pytest.raises(WorldBoundaryError):
        assert_sse_world(
            w,
            {"tenant_id": "world-b", "user_id": "u-a", "owner_id": "u-a"},
            resource_name="orchestration_stream",
        )


def test_sse_same_world_allow():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    assert_sse_world(
        w,
        {"tenant_id": "world-a", "user_id": "u-a", "owner_id": "u-a"},
        resource_name="orchestration_stream",
    )


def test_cache_key_world_qualified():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    key = world_cache_key(w, "session", "s1")
    assert key.startswith("world:world-a:")
    assert parse_world_from_cache_key(key) == "world-a"
    assert_cache_key_world(w, key)


def test_cache_key_cross_world_deny():
    wa = WorldContext(world_id="world-a", principal_id="u-a")
    wb = WorldContext(world_id="world-b", principal_id="u-b")
    key = world_cache_key(wb, "x")
    with pytest.raises(WorldBoundaryError):
        assert_cache_key_world(wa, key)


def test_world_scoped_registry_isolation():
    reg = WorldScopedRegistry()
    wa = WorldContext(world_id="world-a", principal_id="u-a")
    wb = WorldContext(world_id="world-b", principal_id="u-b")
    reg.put(wa, "session", {"id": 1})
    assert reg.get(wa, "session")["id"] == 1
    with pytest.raises(WorldBoundaryError):
        reg.get(wb, "session")


def test_delegation_still_fail_closed_missing_world():
    with pytest.raises(WorldBoundaryError):
        nuha_world_from_fields(user_id="u-a")


def test_indirect_matrix_pass5():
    w = WorldContext(world_id="world-a", principal_id="u-a")
    # A → WS → B
    with pytest.raises(WorldBoundaryError):
        assert_terminal_world(w, user_id="u-a", project_id="p", project_owner_id="u-b")
    # A → SSE → B
    with pytest.raises(WorldBoundaryError):
        assert_sse_world(w, {"tenant_id": "world-b", "user_id": "u-a"})
    # A → MCP → B
    with pytest.raises(WorldBoundaryError):
        assert_mcp_world(w, target_world_id="world-b")
    # A → Cache → B
    key = world_cache_key(WorldContext(world_id="world-b", principal_id="u-b"), "k")
    with pytest.raises(WorldBoundaryError):
        assert_cache_key_world(w, key)
    # A → Delegation → B
    with pytest.raises(WorldBoundaryError):
        assert_delegation_world(w, task_tenant_id="world-b")
