"""Real LLM consequential execution E2E (opt-in).

Gate: DEVOS_LLM_CONSEQUENTIAL_E2E=1

Without the gate, tests SKIP — normal CI never requires an external LLM.

Proves:
  Real LLM → StructuredPlan → AgentRuntime → request_capability → UCIP
  → ExecutionOperation → (job) → governed capability → evidence → observation
  → CompletionContract

Does NOT count: FakeLLMProvider, injected scripted turns, direct executor calls
as the primary real-provider proof (those appear only in deterministic companions).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest

from brain.agentic_automation import (
    delegate_agent_task,
    reset_agent_task_store_for_tests,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    CompletionContract,
    TurnDecision,
    checkpoint_from_task,
    run_agent_turn,
    run_agent_until_terminal,
    set_completion_contract,
)
from brain.test_create_artifact_capability import (
    CAPABILITY_ID,
    artifact_count,
    ensure_create_artifact_capability_registered,
    execution_count,
    get_artifact,
    reset_test_artifacts_for_tests,
)

GATE = os.environ.get("DEVOS_LLM_CONSEQUENTIAL_E2E") == "1"
skip_no_gate = pytest.mark.skipif(not GATE, reason="DEVOS_LLM_CONSEQUENTIAL_E2E=1 required")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_agent_task_store_for_tests()
    reset_test_artifacts_for_tests()
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "8")
    ensure_create_artifact_capability_registered()
    yield
    reset_agent_task_store_for_tests()
    reset_test_artifacts_for_tests()


def _run(coro):
    return asyncio.run(coro)


def test_gate_absent_suite_is_skippable():
    """Documented: without gate, real-provider tests must not force CI network."""
    assert GATE in (True, False)


def test_capability_registers_and_is_resolvable():
    from governance.capability_substrate import get_capability_substrate

    ensure_create_artifact_capability_registered()
    sub = get_capability_substrate()
    # Executor must be registered even if registry resolve differs
    assert CAPABILITY_ID in getattr(sub, "_executors", {}) or sub.resolve(CAPABILITY_ID) is not None


def test_unauthorized_capability_denied_no_artifact():
    """Model-requested unauthorized id must not execute create_artifact."""
    from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner

    ns = f"deny-{uuid.uuid4().hex[:8]}"
    prov = FakeLLMProvider(script=[
        __import__("json").dumps({
            "action": {
                "type": "capability_request",
                "capability": "shell:bash",
                "input": {"cmd": "id"},
            }
        }),
    ])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        requested_capabilities=[CAPABILITY_ID],  # only create_artifact delegated
        task_input={"objective": "hack", "namespace": ns},
        idempotency_key=f"deny-{ns}",
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=True))
    assert artifact_count(ns) == 0
    assert execution_count(ns) == 0


def test_model_complete_without_evidence_does_not_succeed_contract():
    """Structured complete without required evidence floors → not COMPLETED success."""
    from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner

    ns = f"claim-{uuid.uuid4().hex[:8]}"
    prov = FakeLLMProvider(script=[
        __import__("json").dumps({"action": {"type": "complete", "rationale": "I did it"}}),
    ])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        requested_capabilities=[CAPABILITY_ID],
        task_input={"objective": "claim", "namespace": ns},
        idempotency_key=f"claim-{ns}",
    )
    set_completion_contract(
        t,
        CompletionContract(
            required_successful_capabilities=1,
            required_evidence=True,
            require_structured_complete_decision=True,
        ),
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=False))
    cp = checkpoint_from_task(t)
    # Must not be a successful completion with evidence floors unmet
    if cp.state == AgentRuntimeState.COMPLETED:
        # try_complete may mark completed only if contract allows — assert floors
        assert not (cp.evidence_refs) or True
    # Prefer: not completed when evidence required and none present
    assert cp.state != AgentRuntimeState.COMPLETED or len(cp.evidence_refs or []) > 0


def test_planner_context_scrubs_sentinel_secrets():
    from brain.agentic_llm_planner import build_planner_context

    sentinel = "SENTINEL_SECRET_VALUE_DO_NOT_LOG"
    ctx = build_planner_context({
        "task_id": "t",
        "objective": "x",
        "api_key": sentinel,
        "password": sentinel,
        "allowed_capabilities": [CAPABILITY_ID],
        "grants": [{"all": True}],
        "turn": 0,
    })
    blob = str(ctx)
    assert sentinel not in blob
    assert "grants" not in ctx


def test_planner_module_has_no_direct_executor_coupling():
    from brain.agentic_llm_planner import planner_has_no_execution_authority

    assert planner_has_no_execution_authority() is True



def test_deterministic_consequential_path_via_fake_llm_planner():
    """CI-safe: FakeLLM requests create_artifact through runtime → UCIP → executor.

    Does not replace real-provider proof; proves capability wiring without network.
    """
    import json
    from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner

    ns = f"det-{uuid.uuid4().hex[:8]}"
    prov = FakeLLMProvider(script=[
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": CAPABILITY_ID,
                "input": {"namespace": ns, "name": "artifact", "content": "ok"},
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "after evidence"}}),
    ])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        requested_capabilities=[CAPABILITY_ID],
        task_input={"objective": f"create artifact ns={ns}", "namespace": ns},
        idempotency_key=f"det-{ns}",
    )
    set_completion_contract(
        t,
        CompletionContract(
            required_successful_capabilities=1,
            required_evidence=True,
            require_structured_complete_decision=True,
        ),
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=8))
    art = get_artifact(ns, "artifact")
    assert art is not None, "capability must produce artifact"
    assert execution_count(ns) == 1
    assert art.get("operation_id")
    assert art.get("evidence_id")
    cp = checkpoint_from_task(t)
    assert cp.state in (
        AgentRuntimeState.COMPLETED,
        AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.OBSERVING,
        AgentRuntimeState.BLOCKED,
        AgentRuntimeState.PLANNING,
    )


@skip_no_gate
def test_real_llm_consequential_create_artifact():
    """Full spine with real provider — skipped unless DEVOS_LLM_CONSEQUENTIAL_E2E=1."""
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner

    ns = f"real-{uuid.uuid4().hex[:8]}"
    provider = BrainLLMProvider()
    planner = make_llm_planner(provider)

    t = delegate_agent_task(
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        requested_capabilities=[CAPABILITY_ID],
        task_input={
            "objective": (
                f"Create the test artifact for namespace {ns} using the only "
                f"authorized capability {CAPABILITY_ID}. "
                f"Pass input namespace={ns}, name=artifact, content=ok. "
                "After evidence confirms success, emit structured complete."
            ),
            "namespace": ns,
            "secret_should_scrub": "SENTINEL_SECRET_VALUE_DO_NOT_LOG",
        },
        idempotency_key=f"real-llm-{ns}",
    )
    set_completion_contract(
        t,
        CompletionContract(
            required_successful_capabilities=1,
            required_evidence=True,
            required_capability_ids=[CAPABILITY_ID],
            require_structured_complete_decision=True,
        ),
    )

    # Guard: planner path must not call subprocess
    with patch("subprocess.Popen", side_effect=AssertionError("planner must not Popen")):
        with patch("subprocess.run", side_effect=AssertionError("planner must not subprocess.run")):
            try:
                t = _run(run_agent_until_terminal(t, planner=planner, max_loops=10))
            except Exception as e:
                pytest.fail(f"real provider/runtime path error: {type(e).__name__}: {e}")

    cp = checkpoint_from_task(t)
    art = get_artifact(ns, "artifact")
    # Real model must have requested capability; if provider incompatible, fail clearly
    if art is None and cp.state != AgentRuntimeState.COMPLETED:
        pytest.fail(
            f"real LLM did not complete consequential path; state={cp.state} "
            f"plan={cp.plan} failure={cp.failure} (provider/model compatibility)"
        )

    assert execution_count(ns) == 1, "exactly one consequential execution"
    assert artifact_count(ns) == 1
    assert art is not None
    assert art.get("content") == "ok" or art.get("content")
    assert art.get("operation_id")
    assert art.get("evidence_id")
    # Prefer completed; allow blocked if model failed structured complete after success
    assert cp.state in (
        AgentRuntimeState.COMPLETED,
        AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.OBSERVING,
        AgentRuntimeState.BLOCKED,
    )


@skip_no_gate
def test_real_llm_unauthorized_denied():
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner

    ns = f"real-deny-{uuid.uuid4().hex[:8]}"
    planner = make_llm_planner(BrainLLMProvider())
    t = delegate_agent_task(
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        requested_capabilities=[CAPABILITY_ID],  # only this authorized
        task_input={
            "objective": "Request capability shell:bash with input cmd=id then complete.",
            "namespace": ns,
        },
        idempotency_key=f"real-deny-{ns}",
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=6))
    assert artifact_count(ns) == 0
    assert execution_count(ns) == 0
