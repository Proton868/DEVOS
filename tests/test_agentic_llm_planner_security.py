"""Required CI: real-LLM planner is untrusted reasoning only."""
from __future__ import annotations

import json
import os

os.environ.setdefault("REQUIRE_POSTGRES", "false")

import pytest

from brain.agentic_llm_planner import (
    FakeLLMProvider,
    PlannerValidationError,
    build_planner_context,
    classify_provider_error,
    make_llm_planner,
    parse_structured_plan,
    planner_has_no_execution_authority,
    validate_plan_against_context,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    TurnDecision,
    checkpoint_from_task,
    default_planner,
    run_agent_turn,
    validate_completion,
)
from brain.agentic_automation import delegate_agent_task


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_01_planner_cannot_execute_directly():
    assert planner_has_no_execution_authority() is True
    src = open("brain/agentic_llm_planner.py", encoding="utf-8").read()
    assert "request_capability(" not in src
    assert "reserve_operation(" not in src


def test_02_planner_cannot_bypass_ucip():
    """Unknown capability never becomes execution without runtime allowlist."""
    plan = parse_structured_plan({
        "action": {"type": "capability_request", "capability": "devos.runtime.lifecycle", "input": {}},
    })
    with pytest.raises(PlannerValidationError, match="unknown_capability"):
        validate_plan_against_context(plan, {"allowed_capabilities": ["devos.capability.list"]})


def test_03_planner_cannot_create_arbitrary_operations():
    with pytest.raises(PlannerValidationError):
        parse_structured_plan({
            "action": {
                "type": "capability_request",
                "capability": "devos.capability.list",
                "operation_id": "forged-op",
                "input": {},
            }
        })


def test_04_planner_cannot_bypass_authorization_fields():
    with pytest.raises(PlannerValidationError, match="forbidden_plan_key|forbidden_input"):
        parse_structured_plan({
            "action": {
                "type": "capability_request",
                "capability": "devos.capability.list",
                "grants": ["*"],
                "input": {},
            }
        })


def test_05_planner_cannot_weaken_isolation():
    with pytest.raises(PlannerValidationError, match="forbidden_plan_key"):
        parse_structured_plan({
            "isolation_strength": "none",
            "action": {"type": "complete"},
        })


def test_06_planner_cannot_alter_owner_tenant():
    with pytest.raises(PlannerValidationError, match="forbidden_plan_key"):
        parse_structured_plan({
            "owner_id": "attacker",
            "tenant_id": "other",
            "action": {"type": "complete"},
        })


def test_07_planner_cannot_alter_retry_policy():
    with pytest.raises(PlannerValidationError, match="forbidden_plan_key"):
        parse_structured_plan({
            "retry_policy": {"max": 99},
            "action": {"type": "complete"},
        })


def test_08_planner_cannot_fabricate_evidence():
    with pytest.raises(PlannerValidationError, match="forbidden_plan_key"):
        parse_structured_plan({
            "evidence": ["fake"],
            "action": {"type": "complete"},
        })


def test_09_planner_cannot_fabricate_completion():
    prov = FakeLLMProvider(script=["done", "verified", "looks good"])
    planner = make_llm_planner(prov)
    for _ in range(3):
        d = planner({"allowed_capabilities": ["devos.capability.list"], "turn": 0})
        assert d.kind != "complete" or d.kind == "block"
        assert d.kind in ("block", "fail")


def test_10_planner_cannot_request_undeclared_capabilities():
    plan = parse_structured_plan({
        "action": {
            "type": "capability_request",
            "capability": "not.in.allowlist",
            "input": {},
        }
    })
    with pytest.raises(PlannerValidationError, match="unknown_capability"):
        validate_plan_against_context(plan, {"allowed_capabilities": ["devos.capability.list"]})


def test_11_planner_cannot_leak_secrets():
    ctx = {
        "allowed_capabilities": ["devos.capability.list"],
        "api_key": "sk-secret-value",
        "password": "hunter2",
        "credentials": {"token": "abc"},
        "authorization": {"bearer": "xyz"},
        "task_input": {"api_key": "should-not-appear"},
        "objective": "safe goal",
    }
    out = build_planner_context(ctx)
    blob = json.dumps(out)
    assert "sk-secret-value" not in blob
    assert "hunter2" not in blob
    assert "should-not-appear" not in blob
    assert "credentials" not in out


def test_12_malformed_model_output_cannot_execute():
    prov = FakeLLMProvider(script=["{{{{not json"])
    planner = make_llm_planner(prov)
    d = planner({"allowed_capabilities": ["devos.capability.list"]})
    assert d.kind in ("block", "fail")
    assert "planner_invalid" in d.reason or "malformed" in d.reason


def test_13_provider_failure_cannot_execute():
    prov = FakeLLMProvider(raise_exc=TimeoutError("provider timeout"))
    planner = make_llm_planner(prov)
    d = planner({"allowed_capabilities": ["devos.capability.list"]})
    assert d.kind in ("block", "fail")
    assert "timeout" in d.reason or "planner_" in d.reason
    # classify helper
    assert classify_provider_error(TimeoutError("x")) == "planner_timeout"


def test_14_unknown_cannot_become_executing_via_planner():
    from brain.agentic_runtime import can_transition
    assert not can_transition(AgentRuntimeState.UNKNOWN, AgentRuntimeState.EXECUTING)
    # Planner complete while UNKNOWN is runtime-controlled
    t = delegate_agent_task(
        owner_id="sec-unknown",
        requested_capabilities=["devos.capability.list"],
    )
    from brain.agentic_runtime import apply_transition, persist_checkpoint, checkpoint_from_task as cft
    cp = cft(t)
    cp.state = AgentRuntimeState.UNKNOWN
    persist_checkpoint(t, cp)
    prov = FakeLLMProvider(script=[json.dumps({"action": {"type": "complete"}})])
    planner = make_llm_planner(prov)
    t = _run(run_agent_turn(t, planner=planner, execute_capability=False))
    cp2 = cft(t)
    assert cp2.state != AgentRuntimeState.EXECUTING
    assert cp2.state != AgentRuntimeState.COMPLETED or False


def test_15_cancellation_not_overridden_by_planner():
    from brain.agentic_runtime import request_cancel_runtime, checkpoint_from_task as cft
    t = delegate_agent_task(
        owner_id="sec-cancel",
        requested_capabilities=["devos.capability.list"],
    )
    request_cancel_runtime(t)
    prov = FakeLLMProvider(script=[
        json.dumps({"action": {"type": "capability_request", "capability": "devos.capability.list", "input": {}}}),
        json.dumps({"action": {"type": "complete"}}),
    ])
    planner = make_llm_planner(prov)
    t = _run(run_agent_turn(t, planner=planner, execute_capability=True))
    cp = cft(t)
    assert cp.state == AgentRuntimeState.CANCELLED or cp.cancel_requested


def test_fallback_policy_explicit():
    prov = FakeLLMProvider(raise_exc=ConnectionError("down"))
    # Without fallback — block
    p1 = make_llm_planner(prov, fallback_on_provider_error=False)
    d1 = p1({"allowed_capabilities": ["devos.capability.list"]})
    assert d1.kind in ("block", "fail")
    # With fallback — deterministic path only
    prov2 = FakeLLMProvider(raise_exc=ConnectionError("down"))
    p2 = make_llm_planner(
        prov2,
        fallback=default_planner,
        fallback_on_provider_error=True,
        planner_meta={"planner_type": "llm", "provider": "fake"},
    )
    d2 = p2({
        "allowed_capabilities": ["devos.capability.list"],
        "turn": 0,
        "last_observation": None,
    })
    assert d2.kind in ("capability_request", "complete", "block")
    assert "planner=" in (d2.reason or "")


def test_llm_complete_without_evidence_rejected_by_runtime():
    from brain.agentic_runtime import (
        CompletionContract,
        set_completion_contract,
        apply_transition,
        try_complete,
        checkpoint_from_task as cft,
    )
    t = delegate_agent_task(
        owner_id="sec-complete",
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
    prov = FakeLLMProvider(script=[json.dumps({"action": {"type": "complete", "rationale": "verified"}})])
    planner = make_llm_planner(prov)
    d = planner({"allowed_capabilities": ["devos.capability.list"]})
    assert d.kind == "complete"
    cp = cft(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    try_complete(t, cp, d)
    assert cft(t).state != AgentRuntimeState.COMPLETED


def test_max_turns_not_controllable_by_plan():
    with pytest.raises(PlannerValidationError):
        parse_structured_plan({
            "max_turns": 9999,
            "action": {"type": "complete"},
        })
