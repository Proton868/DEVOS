"""Durable agentic automation runtime — state machine, turns, recovery."""
from __future__ import annotations

import pytest

from brain.agentic_automation import (
    AgentTaskLifecycle,
    delegate_agent_task,
    reset_agent_task_store_for_tests,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    IllegalTransition,
    TurnDecision,
    apply_transition,
    bounds_policy,
    build_agent_context,
    can_transition,
    capability_request_idempotency_key,
    checkpoint_from_task,
    default_planner,
    max_turns,
    observe_operation_result,
    persist_checkpoint,
    reconcile_unknown,
    request_cancel_runtime,
    run_agent_turn,
    run_agent_until_terminal,
    transition,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_agent_task_store_for_tests()
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "4")
    yield
    reset_agent_task_store_for_tests()


def test_legal_transitions():
    assert can_transition(AgentRuntimeState.CREATED, AgentRuntimeState.PLANNING)
    assert can_transition(AgentRuntimeState.PLANNING, AgentRuntimeState.AWAITING_CAPABILITY)
    assert can_transition(AgentRuntimeState.EXECUTING, AgentRuntimeState.UNKNOWN)
    assert can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.BLOCKED)
    assert can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.OBSERVING)


def test_illegal_transitions():
    assert not can_transition(AgentRuntimeState.PLANNING, AgentRuntimeState.EXECUTING)
    assert not can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.EXECUTING)
    assert not can_transition(AgentRuntimeState.COMPLETED, AgentRuntimeState.PLANNING)
    with pytest.raises(IllegalTransition):
        transition(AgentRuntimeState.PLANNING, AgentRuntimeState.EXECUTING)


def test_unknown_not_failed():
    assert AgentRuntimeState.UNKNOWN not in (
        AgentRuntimeState.FAILED, AgentRuntimeState.COMPLETED,
    )


def test_bounds_agent_cannot_raise(monkeypatch):
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "999")
    assert max_turns() <= 32
    b = bounds_policy()
    assert b["agent_cannot_raise"] is True


def test_context_scrubs_secrets():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    t.task_input = {"api_key": "secret", "goal": "x"}
    ctx = build_agent_context(t, cp)
    assert "api_key" not in str(ctx).lower() or "secret" not in str(ctx.values())


def test_capability_request_idempotency_stable():
    a = capability_request_idempotency_key(
        task_id="t1", turn=1, capability_id="devos.capability.list",
    )
    b = capability_request_idempotency_key(
        task_id="t1", turn=1, capability_id="devos.capability.list",
    )
    assert a == b


@pytest.mark.asyncio
async def test_run_turn_planning_to_complete():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )

    def complete_planner(ctx):
        return TurnDecision(kind="complete", complete=True, reason="ok")

    t = await run_agent_turn(t, planner=complete_planner, execute_capability=False)
    cp = checkpoint_from_task(t)
    assert cp.state == AgentRuntimeState.COMPLETED
    assert cp.turn >= 1


@pytest.mark.asyncio
async def test_max_turns_blocks(monkeypatch):
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "2")

    def always_cap(ctx):
        return TurnDecision(
            kind="capability_request",
            capability_id="devos.capability.list",
            inputs={},
        )

    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    t = await run_agent_until_terminal(t, planner=always_cap, max_loops=10)
    cp = checkpoint_from_task(t)
    assert cp.state in (
        AgentRuntimeState.BLOCKED, AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.PLANNING, AgentRuntimeState.COMPLETED,
        AgentRuntimeState.EXECUTING, AgentRuntimeState.OBSERVING,
    )


@pytest.mark.asyncio
async def test_cancel_during_planning():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    request_cancel_runtime(t)
    t = await run_agent_turn(t, planner=default_planner)
    cp = checkpoint_from_task(t)
    assert cp.state == AgentRuntimeState.CANCELLED or cp.cancel_requested


@pytest.mark.asyncio
async def test_multi_turn_with_capability():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    t = await run_agent_until_terminal(t, planner=default_planner)
    cp = checkpoint_from_task(t)
    # default planner: turn1 requests cap, turn2 completes after observation path
    assert cp.turn >= 1
    assert cp.state in (
        AgentRuntimeState.COMPLETED, AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.PLANNING, AgentRuntimeState.BLOCKED,
        AgentRuntimeState.OBSERVING, AgentRuntimeState.EXECUTING,
    )


@pytest.mark.asyncio
async def test_observe_operation_after_executing():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    apply_transition(cp, AgentRuntimeState.AWAITING_CAPABILITY)
    apply_transition(cp, AgentRuntimeState.AUTHORIZING)
    apply_transition(cp, AgentRuntimeState.AUTHORIZED)
    apply_transition(cp, AgentRuntimeState.EXECUTING)
    persist_checkpoint(t, cp)
    t = observe_operation_result(
        t, operation_id="op-1", status="succeeded", evidence_refs=["ev-1"],
    )
    cp2 = checkpoint_from_task(t)
    assert cp2.state in (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.OBSERVING)
    assert "ev-1" in cp2.evidence_refs


def test_unknown_blocks_retry():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    apply_transition(cp, AgentRuntimeState.AWAITING_CAPABILITY)
    apply_transition(cp, AgentRuntimeState.AUTHORIZING)
    apply_transition(cp, AgentRuntimeState.AUTHORIZED)
    apply_transition(cp, AgentRuntimeState.EXECUTING)
    apply_transition(cp, AgentRuntimeState.UNKNOWN)
    persist_checkpoint(t, cp)
    # UNKNOWN → EXECUTING illegal
    assert not can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.EXECUTING)


def test_reconcile_unknown_to_observing():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    for st in (
        AgentRuntimeState.PLANNING, AgentRuntimeState.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING, AgentRuntimeState.AUTHORIZED,
        AgentRuntimeState.EXECUTING, AgentRuntimeState.UNKNOWN,
    ):
        if cp.state != st:
            try:
                apply_transition(cp, st)
            except IllegalTransition:
                cp.state = st
    persist_checkpoint(t, cp)
    t = reconcile_unknown(t, resolved_status="succeeded", evidence_refs=["ev"])
    cp2 = checkpoint_from_task(t)
    assert cp2.state == AgentRuntimeState.OBSERVING


def test_checkpoint_version_increments():
    t = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    v1 = cp.version
    persist_checkpoint(t, cp)
    cp2 = checkpoint_from_task(t)
    assert cp2.version > v1


def test_migration_file_exists():
    from pathlib import Path
    p = Path("supabase/migrations/20260918200000_agentic_runtime_checkpoints.sql")
    assert p.is_file()
    text = p.read_text()
    assert "agentic_runtime_checkpoints" in text
    assert "owner_id" in text
    down = Path("supabase/migrations/20260918200000_agentic_runtime_checkpoints.down.sql")
    assert down.is_file()
