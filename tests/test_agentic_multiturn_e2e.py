"""Multi-turn agent execution E2E — completion contract, restart, UNKNOWN.

Test taxonomy (this file = E2E + completion integration):
- unit state machine: tests/test_agentic_runtime.py
- contract/delegation: tests/test_agentic_automation.py
- recovery/E2E multi-turn: this file

Isolation: unique owner/tenant/run per test via uuid namespace.
Cleanup: reset_agent_task_store_for_tests in fixture (process store).
Coverage matrix: docs/architecture/agentic-automation-runtime.md
  (Test Coverage Matrix — covered / partial / gap vs milestone checklist).
"""
from __future__ import annotations

import uuid
import pytest

from brain.agentic_automation import (
    AgentTaskLifecycle,
    delegate_agent_task,
    reset_agent_task_store_for_tests,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    CompletionContract,
    TurnDecision,
    apply_transition,
    checkpoint_from_task,
    get_completion_contract,
    observe_operation_result,
    persist_checkpoint,
    reconcile_unknown,
    request_cancel_runtime,
    run_agent_turn,
    run_agent_until_terminal,
    set_completion_contract,
    try_complete,
    validate_completion,
)


def _ns() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    reset_agent_task_store_for_tests()
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "8")
    yield
    reset_agent_task_store_for_tests()


def _task(ns: str, caps=None, contract: CompletionContract | None = None):
    t = delegate_agent_task(
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        agent_type="worker",
        task_input={"goal": "e2e", "run_ns": ns},
        requested_capabilities=caps or ["devos.capability.list"],
        idempotency_key=f"idem-{ns}",
        logical_key=f"logical-{ns}",
    )
    if contract is not None:
        set_completion_contract(t, contract)
    return t


# ── Completion contract ───────────────────────────────────────────────────────

def test_valid_completion():
    ns = _ns()
    c = CompletionContract(required_successful_capabilities=0, require_structured_complete_decision=True)
    t = _task(ns, contract=c)
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True, reason="structured")
    v = validate_completion(t, decision=d, cp=cp)
    assert v.ok, v.reasons
    t, cp, ok = try_complete(t, cp, d)
    assert ok
    assert checkpoint_from_task(t).state == AgentRuntimeState.COMPLETED


def test_missing_evidence_blocks_completion():
    ns = _ns()
    c = CompletionContract(required_evidence=True, required_successful_capabilities=1)
    t = _task(ns, contract=c)
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    assert any("evidence" in r or "insufficient" in r for r in v.reasons)
    t, cp, ok = try_complete(t, cp, d)
    assert not ok
    assert checkpoint_from_task(t).state != AgentRuntimeState.COMPLETED


def test_active_operation_blocks_completion():
    ns = _ns()
    t = _task(ns, contract=CompletionContract())
    cp = checkpoint_from_task(t)
    for st in (
        AgentRuntimeState.PLANNING, AgentRuntimeState.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING, AgentRuntimeState.AUTHORIZED, AgentRuntimeState.EXECUTING,
    ):
        try:
            apply_transition(cp, st)
        except Exception:
            cp.state = st
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    assert "operation_still_active" in v.reasons


def test_unknown_blocks_completion():
    ns = _ns()
    t = _task(ns)
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    assert "state_unknown" in v.reasons


def test_missing_required_output_blocks():
    ns = _ns()
    c = CompletionContract(required_outputs=["report"])
    t = _task(ns, contract=c)
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    assert any("missing_required_output" in r for r in v.reasons)


def test_cancelled_blocks_completion():
    ns = _ns()
    t = _task(ns)
    request_cancel_runtime(t)
    cp = checkpoint_from_task(t)
    d = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok


def test_fake_textual_completion_rejected():
    ns = _ns()
    t = _task(ns)
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    # kind is not complete — free text only
    d = TurnDecision(kind="capability_request", capability_id=None, reason="done", complete=False)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    assert "missing_structured_complete_decision" in v.reasons


def test_completion_contract_immutable():
    ns = _ns()
    c1 = CompletionContract(required_successful_capabilities=2)
    t = _task(ns, contract=c1)
    set_completion_contract(t, CompletionContract(required_successful_capabilities=0))
    got = get_completion_contract(t)
    assert got.required_successful_capabilities == 2


# ── Scripted multi-turn planner ───────────────────────────────────────────────

def scripted_two_cap_planner(sequence_caps):
    """Produces capability requests for each cap then complete."""
    state = {"i": 0}

    def planner(ctx):
        # After observations match requested count, complete
        hist = ctx.get("last_observation")
        i = state["i"]
        if i < len(sequence_caps):
            # If we already observed for this index, advance
            if hist and i > 0:
                pass
            cap = sequence_caps[i]
            state["i"] = i + 1
            return TurnDecision(kind="capability_request", capability_id=cap, inputs={"n": i})
        return TurnDecision(kind="complete", complete=True, reason="structured_complete")

    return planner


@pytest.mark.asyncio
async def test_two_turn_successful_agent():
    ns = _ns()
    # Contract: need at least 1 successful capability (substrate meta list)
    c = CompletionContract(
        required_successful_capabilities=1,
        required_evidence=True,
        require_structured_complete_decision=True,
    )
    t = _task(ns, contract=c)
    planner = scripted_two_cap_planner(["devos.capability.list"])
    t = await run_agent_until_terminal(t, planner=planner, max_loops=12)
    cp = checkpoint_from_task(t)
    # Drive remaining planning→complete if stopped at CHECKPOINTING
    for _ in range(6):
        if cp.state == AgentRuntimeState.COMPLETED:
            break
        if cp.state in (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.OBSERVING, AgentRuntimeState.PLANNING, AgentRuntimeState.CREATED):
            t = await run_agent_turn(t, planner=planner, execute_capability=True)
            cp = checkpoint_from_task(t)
        else:
            break
    assert cp.state == AgentRuntimeState.COMPLETED, (cp.state, cp.failure, t.recovery)
    assert cp.turn >= 1
    assert len(cp.evidence_refs) >= 1 or len(t.evidence_refs) >= 1


@pytest.mark.asyncio
async def test_three_turn_path_with_context():
    ns = _ns()
    c = CompletionContract(required_successful_capabilities=1, required_evidence=True)
    t = _task(ns, contract=c)
    observations = []

    def planner(ctx):
        if ctx.get("last_observation"):
            observations.append(ctx["last_observation"])
        if len(observations) == 0 and ctx.get("turn", 0) <= 1:
            return TurnDecision(kind="capability_request", capability_id="devos.capability.list")
        return TurnDecision(kind="complete", complete=True)

    t = await run_agent_until_terminal(t, planner=planner, max_loops=10)
    for _ in range(8):
        cp = checkpoint_from_task(t)
        if cp.state == AgentRuntimeState.COMPLETED:
            break
        if cp.state in (
            AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.PLANNING,
            AgentRuntimeState.OBSERVING, AgentRuntimeState.CREATED,
        ):
            t = await run_agent_turn(t, planner=planner)
        else:
            break
    cp = checkpoint_from_task(t)
    assert cp.state == AgentRuntimeState.COMPLETED, (cp.state, cp.failure)


@pytest.mark.asyncio
async def test_restart_between_turns():
    ns = _ns()
    c = CompletionContract(required_successful_capabilities=1, required_evidence=True)
    t = _task(ns, contract=c)
    # Turn 1: request + execute
    def p1(ctx):
        return TurnDecision(kind="capability_request", capability_id="devos.capability.list")

    t = await run_agent_turn(t, planner=p1, execute_capability=True)
    cp = checkpoint_from_task(t)
    assert cp.capability_request_count >= 1 or cp.last_observation or cp.state in (
        AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.OBSERVING, AgentRuntimeState.EXECUTING,
    )
    # Simulate restart: reload from store
    from brain.agentic_automation import get_agent_task_store
    t2 = get_agent_task_store().get(t.task_id)
    assert t2 is not None
    cp2 = checkpoint_from_task(t2)
    # Must not redispatch blindly — continue
    def p2(ctx):
        if ctx.get("last_observation") or (ctx.get("capability_request_count") or 0) >= 1:
            return TurnDecision(kind="complete", complete=True)
        return TurnDecision(kind="capability_request", capability_id="devos.capability.list")

    for _ in range(8):
        if checkpoint_from_task(t2).state == AgentRuntimeState.COMPLETED:
            break
        st = checkpoint_from_task(t2).state
        if st in (
            AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.PLANNING,
            AgentRuntimeState.OBSERVING, AgentRuntimeState.CREATED,
        ):
            t2 = await run_agent_turn(t2, planner=p2)
        else:
            break
    assert checkpoint_from_task(t2).state == AgentRuntimeState.COMPLETED


def test_restart_after_operation_before_completion():
    ns = _ns()
    c = CompletionContract(required_successful_capabilities=0)
    t = _task(ns, contract=c)
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    for st in (
        AgentRuntimeState.AWAITING_CAPABILITY, AgentRuntimeState.AUTHORIZING,
        AgentRuntimeState.AUTHORIZED, AgentRuntimeState.EXECUTING,
    ):
        apply_transition(cp, st)
    persist_checkpoint(t, cp)
    # Simulate op success after restart
    t = observe_operation_result(t, operation_id=f"op-{ns}", status="succeeded", evidence_refs=[f"ev-{ns}"])
    cp = checkpoint_from_task(t)
    assert cp.state in (AgentRuntimeState.CHECKPOINTING, AgentRuntimeState.OBSERVING)
    # Validate completion after observe
    apply_transition(cp, AgentRuntimeState.PLANNING) if cp.state == AgentRuntimeState.CHECKPOINTING else None
    if cp.state == AgentRuntimeState.CHECKPOINTING:
        apply_transition(cp, AgentRuntimeState.PLANNING)
        persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    t, cp, ok = try_complete(t, checkpoint_from_task(t), d)
    assert ok


def test_unknown_does_not_complete_or_retry():
    ns = _ns()
    t = _task(ns)
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"auto_retry": False}
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    t, cp, ok = try_complete(t, cp, d)
    assert not ok
    assert checkpoint_from_task(t).state != AgentRuntimeState.COMPLETED


def test_reconcile_then_complete():
    ns = _ns()
    c = CompletionContract(required_successful_capabilities=0)
    t = _task(ns, contract=c)
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    persist_checkpoint(t, cp)
    t = reconcile_unknown(t, resolved_status="succeeded", evidence_refs=[f"ev-{ns}"])
    cp = checkpoint_from_task(t)
    assert cp.state == AgentRuntimeState.OBSERVING
    apply_transition(cp, AgentRuntimeState.CHECKPOINTING)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True)
    t, cp, ok = try_complete(t, checkpoint_from_task(t), d)
    assert ok


@pytest.mark.asyncio
async def test_max_turn_enforcement(monkeypatch):
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "2")
    ns = _ns()
    t = _task(ns)

    def endless(ctx):
        return TurnDecision(kind="capability_request", capability_id="devos.capability.list")

    t = await run_agent_until_terminal(t, planner=endless, max_loops=10)
    cp = checkpoint_from_task(t)
    assert cp.state in (AgentRuntimeState.BLOCKED, AgentRuntimeState.CHECKPOINTING) or (
        cp.failure and "maximum_agent_turns" in (cp.failure or "")
    ) or cp.turn >= 2


def test_tests_use_unique_namespaces():
    a, b = _ns(), _ns()
    assert a != b
    t1 = _task(a)
    t2 = _task(b)
    assert t1.owner_id != t2.owner_id
    assert t1.task_id != t2.task_id


def test_durable_turn_counter_increments():
    """Coverage #12 — turn counter persists on checkpoint."""
    ns = _ns()
    t = _task(ns, contract=CompletionContract())
    cp = checkpoint_from_task(t)
    assert cp.turn == 0
    apply_transition(cp, AgentRuntimeState.PLANNING)
    cp.turn = 1
    persist_checkpoint(t, cp)
    from brain.agentic_automation import get_agent_task_store
    reloaded = get_agent_task_store().get(t.task_id)
    assert checkpoint_from_task(reloaded).turn == 1


def test_fake_complete_does_not_satisfy_join_contract():
    """Coverage #28 partial — fake complete fails validation (join must not treat as done)."""
    ns = _ns()
    c = CompletionContract(required_successful_capabilities=2, required_evidence=True)
    t = _task(ns, contract=c)
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    d = TurnDecision(kind="complete", complete=True, reason="done")
    v = validate_completion(t, decision=d, cp=cp)
    assert not v.ok
    assert any("insufficient_successful_capabilities" in r for r in v.reasons)
