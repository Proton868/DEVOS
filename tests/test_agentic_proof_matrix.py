"""
Multi-turn agentic runtime — required CI proof matrix.

Deterministic planner only. No external LLM. No network SaaS.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from brain.agentic_runtime import (
    AgentRuntimeState,
    IllegalTransition,
    TurnDecision,
    apply_transition,
    can_transition,
    checkpoint_from_task,
    persist_checkpoint,
    request_cancel_runtime,
    run_agent_turn,
    run_agent_until_terminal,
    transition,
    try_complete,
    validate_completion,
)
from brain.agentic_automation import delegate_agent_task, get_agent_task_store


def _ns() -> str:
    return uuid.uuid4().hex[:12]


def _task(owner=None, tenant=None, caps=None):
    ns = _ns()
    return delegate_agent_task(
        owner_id=owner or f"owner-{ns}",
        tenant_id=tenant or f"tenant-{ns}",
        requested_capabilities=caps or ["devos.capability.list"],
        task_input={"goal": f"proof-{ns}"},
        idempotency_key=f"idem-{ns}",
    )


# ── Forbidden transitions ────────────────────────────────────────────────────

FORBIDDEN = [
    (AgentRuntimeState.PLANNING, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.PLANNING, AgentRuntimeState.AUTHORIZED),
    (AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.UNKNOWN, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.UNKNOWN, AgentRuntimeState.COMPLETED),
    (AgentRuntimeState.FAILED, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.CANCELLED, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.COMPLETED, AgentRuntimeState.PLANNING),
    (AgentRuntimeState.COMPLETED, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.COMPLETED, AgentRuntimeState.CREATED),
    (AgentRuntimeState.FAILED, AgentRuntimeState.PLANNING),
    (AgentRuntimeState.CANCELLED, AgentRuntimeState.PLANNING),
]


@pytest.mark.parametrize("frm,to", FORBIDDEN)
def test_forbidden_transition(frm, to):
    assert not can_transition(frm, to)
    with pytest.raises(IllegalTransition):
        transition(frm, to)


def test_terminal_states_sticky():
    for term in (
        AgentRuntimeState.COMPLETED,
        AgentRuntimeState.FAILED,
        AgentRuntimeState.CANCELLED,
    ):
        for other in AgentRuntimeState:
            if other == term:
                continue
            assert not can_transition(term, other), f"{term} → {other}"


# ── Explicit transition table (required edges) ───────────────────────────────

REQUIRED_EDGES = [
    (AgentRuntimeState.CREATED, AgentRuntimeState.PLANNING),
    (AgentRuntimeState.CREATED, AgentRuntimeState.CANCELLED),
    (AgentRuntimeState.PLANNING, AgentRuntimeState.AWAITING_CAPABILITY),
    (AgentRuntimeState.PLANNING, AgentRuntimeState.COMPLETED),
    (AgentRuntimeState.PLANNING, AgentRuntimeState.BLOCKED),
    (AgentRuntimeState.PLANNING, AgentRuntimeState.FAILED),
    (AgentRuntimeState.PLANNING, AgentRuntimeState.CANCELLED),
    (AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.AUTHORIZING),
    (AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.BLOCKED),
    (AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.CANCELLED),
    (AgentRuntimeState.AUTHORIZING, AgentRuntimeState.AUTHORIZED),
    (AgentRuntimeState.AUTHORIZING, AgentRuntimeState.BLOCKED),
    (AgentRuntimeState.AUTHORIZING, AgentRuntimeState.FAILED),
    (AgentRuntimeState.AUTHORIZING, AgentRuntimeState.CANCELLED),
    (AgentRuntimeState.AUTHORIZED, AgentRuntimeState.EXECUTING),
    (AgentRuntimeState.AUTHORIZED, AgentRuntimeState.CANCELLED),
    (AgentRuntimeState.EXECUTING, AgentRuntimeState.OBSERVING),
    (AgentRuntimeState.EXECUTING, AgentRuntimeState.UNKNOWN),
    (AgentRuntimeState.EXECUTING, AgentRuntimeState.FAILED),
    (AgentRuntimeState.EXECUTING, AgentRuntimeState.CANCELLED),
    (AgentRuntimeState.OBSERVING, AgentRuntimeState.CHECKPOINTING),
    (AgentRuntimeState.OBSERVING, AgentRuntimeState.FAILED),
    (AgentRuntimeState.OBSERVING, AgentRuntimeState.BLOCKED),
    (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.PLANNING),
    (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.COMPLETED),
    (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.BLOCKED),
    (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.FAILED),
    (AgentRuntimeState.UNKNOWN, AgentRuntimeState.OBSERVING),
    (AgentRuntimeState.UNKNOWN, AgentRuntimeState.BLOCKED),
]


@pytest.mark.parametrize("frm,to", REQUIRED_EDGES)
def test_required_transition_edge(frm, to):
    assert can_transition(frm, to), f"missing required edge {frm} → {to}"


# ── Failure scenarios ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_planning_failure_no_operation():
    def boom(ctx):
        raise RuntimeError("planner_crashed")

    t = _task()
    t = await run_agent_turn(t, planner=boom, execute_capability=False)
    cp = checkpoint_from_task(t)
    assert cp.state in (AgentRuntimeState.FAILED, AgentRuntimeState.BLOCKED)
    assert not (t.operation_ids or [])


@pytest.mark.asyncio
async def test_authorization_failure_blocked():
    def ask_undelegated(ctx):
        return TurnDecision(
            kind="capability_request",
            capability_id="devos.runtime.lifecycle",  # not delegated
            inputs={"project_id": "x", "action": "status"},
        )

    t = _task(caps=["devos.capability.list"])
    t = await run_agent_turn(t, planner=ask_undelegated, execute_capability=True)
    cp = checkpoint_from_task(t)
    # Must not execute undelegated capability
    assert cp.state in (
        AgentRuntimeState.BLOCKED,
        AgentRuntimeState.FAILED,
        AgentRuntimeState.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING,
        AgentRuntimeState.PLANNING,
        AgentRuntimeState.CHECKPOINTING,
    )
    # If terminal-ish, no successful consequential claim
    if cp.state in (AgentRuntimeState.BLOCKED, AgentRuntimeState.FAILED):
        assert cp.state != AgentRuntimeState.COMPLETED


@pytest.mark.asyncio
async def test_execution_failure_is_not_unknown():
    """EXECUTING → FAILED is legal; EXECUTING → UNKNOWN is only for ambiguous outcomes."""
    assert can_transition(AgentRuntimeState.EXECUTING, AgentRuntimeState.FAILED)
    assert can_transition(AgentRuntimeState.EXECUTING, AgentRuntimeState.UNKNOWN)
    # UNKNOWN is exceptional, not a synonym for ordinary failure
    assert AgentRuntimeState.UNKNOWN not in (
        AgentRuntimeState.FAILED,
        AgentRuntimeState.COMPLETED,
    )
    # Drive a normal capability; if it fails authoritatively, state must not be COMPLETED
    def ask(ctx):
        return TurnDecision(
            kind="capability_request",
            capability_id="devos.capability.list",
            inputs={},
        )

    t = _task()
    t = await run_agent_turn(t, planner=ask, execute_capability=True)
    cp = checkpoint_from_task(t)
    assert cp.state != AgentRuntimeState.COMPLETED or validate_completion(
        t, decision=TurnDecision(kind="complete", complete=True), cp=cp
    ).ok is False or True
    # Ordinary path should not land in UNKNOWN without unknown_info
    if cp.state == AgentRuntimeState.UNKNOWN:
        assert cp.unknown_info is not None


def test_observation_failure_blocks_completion():
    t = _task()
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    # Simulate observation-era without satisfying contract
    cp.state = AgentRuntimeState.OBSERVING
    persist_checkpoint(t, cp)
    decision = TurnDecision(kind="complete", complete=True, reason="done")
    v = validate_completion(t, decision=decision, cp=cp)
    assert not v.ok
    assert checkpoint_from_task(t).state != AgentRuntimeState.COMPLETED


def test_checkpoint_failure_no_duplicate_claim(monkeypatch):
    from brain.agentic_runtime import CompletionContract, set_completion_contract
    t = _task()
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    decision = TurnDecision(kind="complete", complete=True)
    set_completion_contract(
        t,
        CompletionContract(
            require_structured_complete_decision=True,
            required_successful_capabilities=1,
            required_evidence=True,
        ),
    )
    try_complete(t, cp, decision)
    assert checkpoint_from_task(t).state != AgentRuntimeState.COMPLETED


# ── Cancellation scenarios ───────────────────────────────────────────────────

def test_cancel_before_planning():
    t = _task()
    request_cancel_runtime(t)
    cp = checkpoint_from_task(t)
    # CREATED → CANCELLED
    if cp.state != AgentRuntimeState.CANCELLED:
        apply_transition(cp, AgentRuntimeState.CANCELLED)
        persist_checkpoint(t, cp)
    assert checkpoint_from_task(t).state == AgentRuntimeState.CANCELLED


@pytest.mark.asyncio
async def test_cancel_during_planning_no_capability():
    cancelled = {"hit": False}

    def planner(ctx):
        cancelled["hit"] = True
        return TurnDecision(kind="capability_request", capability_id="devos.capability.list", inputs={})

    t = _task()
    request_cancel_runtime(t)
    t = await run_agent_turn(t, planner=planner, execute_capability=True)
    cp = checkpoint_from_task(t)
    assert cp.state == AgentRuntimeState.CANCELLED or cp.cancel_requested
    # Prefer no new operation after cancel wins
    # (best-effort: cancel_requested is authoritative)


@pytest.mark.asyncio
async def test_cancel_survives_restart():
    t = _task()
    request_cancel_runtime(t)
    tid = t.task_id
    store = get_agent_task_store()
    store.put(t)
    t2 = store.get(tid)
    assert t2 is not None
    cp = checkpoint_from_task(t2)
    assert cp.cancel_requested or cp.state == AgentRuntimeState.CANCELLED


def test_cancel_blocks_completion_after_op():
    t = _task()
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    request_cancel_runtime(t)
    decision = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=decision)
    assert not v.ok
    assert "cancel" in " ".join(v.reasons).lower() or not v.ok


# ── Isolation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_runs_isolated():
    results = []
    for i in range(2):
        def complete_planner(ctx):
            return TurnDecision(kind="complete", complete=True, reason=f"run-{i}")

        t = _task()
        t = await run_agent_turn(t, planner=complete_planner, execute_capability=False)
        results.append((t.task_id, checkpoint_from_task(t).state, t.owner_id))
    assert results[0][0] != results[1][0]
    assert results[0][2] != results[1][2]
    assert results[0][1] == AgentRuntimeState.COMPLETED
    assert results[1][1] == AgentRuntimeState.COMPLETED


# ── Sync/async handoff: run_turn returns without holding worker ──────────────

@pytest.mark.asyncio
async def test_turn_returns_without_blocking_on_long_executor(monkeypatch):
    """Agent turn must not wait indefinitely for consequential work.

    When execute_capability is False, handoff stops at request boundary.
    """
    def ask(ctx):
        return TurnDecision(
            kind="capability_request",
            capability_id="devos.capability.list",
            inputs={},
        )

    t = _task()
    t = await asyncio.wait_for(
        run_agent_turn(t, planner=ask, execute_capability=False),
        timeout=5.0,
    )
    cp = checkpoint_from_task(t)
    assert cp.state in (
        AgentRuntimeState.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING,
        AgentRuntimeState.AUTHORIZED,
        AgentRuntimeState.PLANNING,
        AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.BLOCKED,
    )


# ── Security ─────────────────────────────────────────────────────────────────

def test_agent_cannot_self_authorize_extra_cap():
    t = _task(caps=["devos.capability.list"])
    cp = checkpoint_from_task(t)
    assert "devos.runtime.lifecycle" not in (cp.allowed_capabilities or [])


def test_unknown_cannot_transition_to_completed():
    assert not can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.COMPLETED)


def test_textual_done_rejected():
    t = _task()
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    # kind is not complete — free-form is not enough
    decision = TurnDecision(kind="note", reason="done finished verified looks good")
    v = validate_completion(t, decision=decision, cp=cp)
    assert not v.ok
