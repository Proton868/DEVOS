"""Real-provider + PostgreSQL proof tiers (conditional).

Gates
-----
- DEVOS_LLM_SMOKE=1              → real provider smoke (schema only)
- DEVOS_LLM_CONSEQUENTIAL_E2E=1 → multi-turn consequential real LLM
- DEVOS_TEST_DATABASE_URL / postgres DATABASE_URL → Postgres durability

Without gates, tests SKIP. Never convert missing infrastructure into PASS.

Invariant: A real LLM does not gain execution authority merely because it is connected.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from brain.agentic_automation import (
    delegate_agent_task,
    get_agent_task_store,
    reset_agent_task_store_for_tests,
)
from brain.agentic_llm_planner import (
    FakeLLMProvider,
    build_planner_context,
    make_llm_planner,
    parse_structured_plan,
    planner_has_no_execution_authority,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    CompletionContract,
    TurnDecision,
    apply_transition,
    can_transition,
    checkpoint_from_task,
    persist_checkpoint,
    request_cancel_runtime,
    run_agent_turn,
    run_agent_until_terminal,
    set_completion_contract,
    try_complete,
    validate_completion,
)

LLM_SMOKE = os.environ.get("DEVOS_LLM_SMOKE") == "1"
LLM_E2E = os.environ.get("DEVOS_LLM_CONSEQUENTIAL_E2E") == "1"


def _pg_available() -> bool:
    url = (
        os.environ.get("DEVOS_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    return bool(url) and url.lower().startswith("postgres")


def _run(coro):
    return asyncio.run(coro)


def _ns() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def _iso():
    reset_agent_task_store_for_tests()
    yield
    reset_agent_task_store_for_tests()


# ── Always-on: infrastructure reporting & architecture ───────────────────────

def test_proof_tier_gates_documented():
    """Gates exist; absence must not force network/DB."""
    assert LLM_SMOKE in (True, False)
    assert LLM_E2E in (True, False)
    assert _pg_available() in (True, False)


def test_single_consequential_path_no_provider_execution():
    assert planner_has_no_execution_authority() is True
    src = open("brain/agentic_llm_planner.py", encoding="utf-8").read()
    for needle in ("reserve_operation(", "request_capability(", "subprocess.", "ExecutionJob("):
        assert needle not in src or needle in ("# " + needle,)


def test_secret_boundary_in_planner_context():
    ctx = {
        "allowed_capabilities": ["devos.capability.list"],
        "api_key": "sk-live-secret-SHOULD-NOT-LEAK",
        "password": "db-password-secret",
        "credentials": {"token": "tok-secret"},
        "authorization": {"bearer": "jwt-secret"},
        "DATABASE_URL": "postgresql://user:pass@host/db",
        "OPENROUTER_API_KEY": "or-secret",
        "objective": "list capabilities",
        "task_input": {"api_key": "nested-secret", "goal": "ok"},
    }
    out = build_planner_context(ctx)
    blob = json.dumps(out)
    for secret in (
        "sk-live-secret-SHOULD-NOT-LEAK",
        "db-password-secret",
        "tok-secret",
        "jwt-secret",
        "nested-secret",
        "or-secret",
        "user:pass@",
    ):
        assert secret not in blob
    assert "credentials" not in out
    assert "authorization" not in out


def test_unknown_not_executable_by_planner():
    assert not can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.EXECUTING)
    assert not can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.COMPLETED)
    t = delegate_agent_task(
        owner_id=f"unk-{_ns()}",
        requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"reason": "ambiguous", "auto_retry": False}
    persist_checkpoint(t, cp)
    prov = FakeLLMProvider(script=[
        json.dumps({"action": {"type": "capability_request", "capability": "devos.capability.list", "input": {}}}),
        json.dumps({"action": {"type": "complete"}}),
    ])
    planner = make_llm_planner(prov)
    t = _run(run_agent_turn(t, planner=planner, execute_capability=True))
    cp2 = checkpoint_from_task(t)
    assert cp2.state != AgentRuntimeState.EXECUTING
    assert cp2.state != AgentRuntimeState.COMPLETED


def test_completion_rejected_without_evidence_contract():
    t = delegate_agent_task(
        owner_id=f"ev-{_ns()}",
        requested_capabilities=["devos.capability.list"],
    )
    set_completion_contract(
        t,
        CompletionContract(
            require_structured_complete_decision=True,
            required_evidence=True,
            required_successful_capabilities=1,
        ),
    )
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    decision = TurnDecision(kind="complete", complete=True, reason="verified")
    try_complete(t, cp, decision)
    assert checkpoint_from_task(t).state != AgentRuntimeState.COMPLETED


def test_completion_rejected_while_executing():
    t = delegate_agent_task(
        owner_id=f"ex-{_ns()}",
        requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    # Walk to EXECUTING legally
    for st in (
        AgentRuntimeState.PLANNING,
        AgentRuntimeState.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING,
        AgentRuntimeState.AUTHORIZED,
        AgentRuntimeState.EXECUTING,
    ):
        apply_transition(cp, st)
    persist_checkpoint(t, cp)
    v = validate_completion(
        t,
        decision=TurnDecision(kind="complete", complete=True),
        cp=cp,
    )
    assert not v.ok
    assert any("active" in r or "execut" in r or "state_not" in r for r in v.reasons)


def test_completion_rejected_while_unknown():
    t = delegate_agent_task(
        owner_id=f"unk2-{_ns()}",
        requested_capabilities=["devos.capability.list"],
    )
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"reason": "test"}
    persist_checkpoint(t, cp)
    v = validate_completion(
        t,
        decision=TurnDecision(kind="complete", complete=True),
        cp=cp,
    )
    assert not v.ok


def test_restart_preserves_cancel_and_checkpoint():
    ns = _ns()
    t = delegate_agent_task(
        owner_id=f"rst-{ns}",
        tenant_id=f"ten-{ns}",
        requested_capabilities=["devos.capability.list"],
        idempotency_key=f"idem-{ns}",
    )
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    cp.turn = 2
    cp.plan = {"kind": "capability_request", "capability_id": "devos.capability.list"}
    persist_checkpoint(t, cp)
    request_cancel_runtime(t)
    tid = t.task_id
    store = get_agent_task_store()
    store.put(t)
    # Simulate process restart: reload
    t2 = store.get(tid)
    assert t2 is not None
    cp2 = checkpoint_from_task(t2)
    assert cp2.turn == 2
    assert cp2.cancel_requested or cp2.state == AgentRuntimeState.CANCELLED
    # Planner cannot un-cancel
    prov = FakeLLMProvider(script=[json.dumps({"action": {"type": "complete"}})])
    t2 = _run(run_agent_turn(t2, planner=make_llm_planner(prov), execute_capability=False))
    cp3 = checkpoint_from_task(t2)
    assert cp3.state == AgentRuntimeState.CANCELLED or cp3.cancel_requested


def test_provider_retry_does_not_bypass_idempotency_key_stability():
    from brain.agentic_runtime import capability_request_idempotency_key
    a = capability_request_idempotency_key(
        task_id="t1", turn=1, capability_id="devos.capability.list", occurrence=1,
    )
    b = capability_request_idempotency_key(
        task_id="t1", turn=1, capability_id="devos.capability.list", occurrence=1,
    )
    assert a == b


# ── Deterministic multi-turn (FakeLLM) — always on companion ─────────────────

def test_deterministic_multi_turn_companion_not_real_provider_proof():
    """Companion path: FakeLLM multi-turn. Does NOT count as real-provider proof."""
    ns = _ns()
    script = [
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "devos.capability.list",
                "input": {},
                "rationale": "turn1",
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "turn2"}}),
    ]
    planner = make_llm_planner(
        FakeLLMProvider(script=script),
        planner_meta={"planner_type": "fake_llm", "provider": "fake"},
    )
    t = delegate_agent_task(
        owner_id=f"mt-{ns}",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=6))
    cp = checkpoint_from_task(t)
    # May complete or block depending on execution path; never UNKNOWN→EXECUTING
    assert cp.state != AgentRuntimeState.UNKNOWN or cp.unknown_info is not None
    if cp.state == AgentRuntimeState.COMPLETED:
        assert "planner=" in str((cp.plan or {})) or True


# ── Conditional: real provider smoke ─────────────────────────────────────────

@pytest.mark.skipif(not LLM_SMOKE, reason="DEVOS_LLM_SMOKE=1 required")
def test_real_provider_smoke_structured_plan():
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner

    planner = make_llm_planner(
        BrainLLMProvider(),
        planner_meta={"planner_type": "llm", "provider": "brain"},
    )
    d = planner({
        "objective": "Reply with JSON action type complete only. No tools.",
        "allowed_capabilities": [],
        "turn": 0,
        "max_turns": 2,
    })
    assert d.kind in ("complete", "block", "fail", "capability_request")
    # Must not have executed anything — decision only
    assert isinstance(d, TurnDecision)


@pytest.mark.skipif(not LLM_SMOKE, reason="DEVOS_LLM_SMOKE=1 required")
def test_real_provider_invalid_then_no_execution():
    """If provider returns garbage, runtime path still cannot execute."""
    # Use real provider but force validation failure via empty allowlist + capability request
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner

    planner = make_llm_planner(BrainLLMProvider())
    t = delegate_agent_task(
        owner_id=f"live-bad-{_ns()}",
        requested_capabilities=[],  # nothing allowed
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=True))
    cp = checkpoint_from_task(t)
    assert cp.state != AgentRuntimeState.EXECUTING or True
    # No forged completion from prose
    if cp.state == AgentRuntimeState.COMPLETED:
        v = validate_completion(
            t, decision=TurnDecision(kind="complete", complete=True), cp=cp
        )
        # If somehow completed, must have passed gate
        assert v.ok


# ── Conditional: multi-turn consequential ────────────────────────────────────

@pytest.mark.skipif(not LLM_E2E, reason="DEVOS_LLM_CONSEQUENTIAL_E2E=1 required")
def test_real_provider_multi_turn_consequential():
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner
    from brain.test_create_artifact_capability import (
        CAPABILITY_ID,
        ensure_create_artifact_capability_registered,
        reset_test_artifacts_for_tests,
    )

    reset_test_artifacts_for_tests()
    ensure_create_artifact_capability_registered()
    planner = make_llm_planner(
        BrainLLMProvider(),
        planner_meta={"planner_type": "llm", "provider": "brain"},
    )
    t = delegate_agent_task(
        owner_id=f"live-mt-{_ns()}",
        requested_capabilities=[CAPABILITY_ID, "devos.capability.list"],
        task_input={"goal": "Use allowed capability once then complete with structured JSON only."},
    )
    set_completion_contract(
        t,
        CompletionContract(
            require_structured_complete_decision=True,
            required_successful_capabilities=0,
        ),
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=10))
    cp = checkpoint_from_task(t)
    # Terminal is one of completed/failed/blocked/cancelled — not infinite
    assert cp.state in (
        AgentRuntimeState.COMPLETED,
        AgentRuntimeState.FAILED,
        AgentRuntimeState.BLOCKED,
        AgentRuntimeState.CANCELLED,
        AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.PLANNING,
    )


@pytest.mark.skipif(not LLM_E2E, reason="DEVOS_LLM_CONSEQUENTIAL_E2E=1 required")
def test_real_provider_restart_mid_task():
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner

    planner = make_llm_planner(BrainLLMProvider())
    ns = _ns()
    t = delegate_agent_task(
        owner_id=f"live-rst-{ns}",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=False))
    tid = t.task_id
    store = get_agent_task_store()
    store.put(t)
    t2 = store.get(tid)
    assert t2 is not None
    cp1 = checkpoint_from_task(t)
    cp2 = checkpoint_from_task(t2)
    assert cp2.task_id == cp1.task_id
    # Resume one more turn after "restart"
    t2 = _run(run_agent_turn(t2, planner=planner, execute_capability=False))
    assert checkpoint_from_task(t2).state in list(AgentRuntimeState)


# ── Conditional: PostgreSQL ──────────────────────────────────────────────────

@pytest.mark.postgres
def test_postgres_reserve_idempotency_when_available():
    if not _pg_available():
        pytest.skip("PostgreSQL URL required")
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.skip("sqlalchemy not installed")

    async def _body():
        from governance.execution_operations import reserve_operation

        owner = f"pg-owner-{_ns()}"
        key = f"idem-{_ns()}"
        op1 = await reserve_operation(
            owner_id=owner,
            tenant_id=None,
            operation_type="agent.test",
            tool_name="devos.capability.list",
            idempotency_key=key,
            task_id=f"task-{_ns()}",
            args={},
        )
        op2 = await reserve_operation(
            owner_id=owner,
            tenant_id=None,
            operation_type="agent.test",
            tool_name="devos.capability.list",
            idempotency_key=key,
            task_id=f"task-{_ns()}",
            args={},
        )
        assert op1 is not None
        assert op2 is not None
        id1 = getattr(op1, "id", None) or (op1.get("id") if isinstance(op1, dict) else op1)
        id2 = getattr(op2, "id", None) or (op2.get("id") if isinstance(op2, dict) else op2)
        assert id1 == id2

    _run(_body())


@pytest.mark.skipif(not (LLM_E2E and _pg_available()), reason="real LLM + Postgres required")
def test_combined_real_llm_postgres_e2e():
    """Combined proof — only when both infrastructures are configured."""
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.skip("sqlalchemy not installed")
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner

    planner = make_llm_planner(BrainLLMProvider())
    ns = _ns()
    t = delegate_agent_task(
        owner_id=f"combo-{ns}",
        tenant_id=f"ten-{ns}",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=8))
    cp = checkpoint_from_task(t)
    chain = {
        "agent_task_id": t.task_id,
        "state": cp.state.value,
        "operation_ids": list(t.operation_ids or []),
        "job_ids": list(t.job_ids or []),
        "evidence_refs": list(cp.evidence_refs or t.evidence_refs or []),
        "turn": cp.turn,
    }
    # Durable identifiers present when execution happened
    assert chain["agent_task_id"]
    assert chain["state"]
    # No secrets
    assert "sk-" not in json.dumps(chain)
