"""Governed agentic automation — delegation, UCIP, UNKNOWN, AGENT step."""
from __future__ import annotations

import pytest

from brain.agentic_automation import (
    AgentTaskLifecycle,
    DelegationError,
    delegate_agent_task,
    request_capability,
    record_plan,
    mark_unknown,
    mark_completed,
    mark_failed,
    cancel_task,
    assert_owner,
    reject_fabricated_evidence,
    reset_agent_task_store_for_tests,
    execute_agent_step_body,
    agent_task_idempotency_key,
)
from brain.workflow import Workflow, WorkflowStep, StepType
from brain.workflow_store import build_execution_snapshot
from brain.workflow_executor import (
    ExecutionState,
    run_from_snapshot,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_DENIED,
)
from brain.automation_orchestration import select_eligible_steps
from brain.automation_parallel import execute_parallel_graph


@pytest.fixture(autouse=True)
def _reset():
    reset_agent_task_store_for_tests()
    yield
    reset_agent_task_store_for_tests()


def test_agent_task_durable_creation():
    t = delegate_agent_task(
        owner_id="u1",
        agent_type="worker",
        task_input={"goal": "inspect"},
        requested_capabilities=["devos.capability.list"],
    )
    assert t.task_id.startswith("agt_")
    assert t.status in (AgentTaskLifecycle.PENDING, AgentTaskLifecycle.FAILED)
    assert "devos.capability.list" in t.allowed_capabilities or t.error


def test_task_idempotency():
    a = delegate_agent_task(
        owner_id="u1",
        task_input={"goal": "x"},
        requested_capabilities=["devos.capability.list"],
        idempotency_key="same-key",
    )
    b = delegate_agent_task(
        owner_id="u1",
        task_input={"goal": "x"},
        requested_capabilities=["devos.capability.list"],
        idempotency_key="same-key",
    )
    assert a.task_id == b.task_id


def test_fake_client_grant_denied():
    with pytest.raises(DelegationError) as ei:
        delegate_agent_task(
            owner_id="u1",
            requested_capabilities=["devos.capability.list"],
            client_supplied_grants=True,
        )
    assert ei.value.code == "CLIENT_GRANT_REJECTED"


def test_privilege_escalation_forbidden_cap():
    t = delegate_agent_task(
        owner_id="u1",
        requested_capabilities=["agent.execute_anything", "shell_raw"],
    )
    assert t.allowed_capabilities == []
    assert t.status == AgentTaskLifecycle.FAILED


def test_owner_isolation():
    t = delegate_agent_task(owner_id="u1", requested_capabilities=["devos.capability.list"])
    with pytest.raises(DelegationError) as ei:
        assert_owner(t, "u2")
    assert ei.value.code == "OWNER_ISOLATION"


def test_tenant_isolation():
    t = delegate_agent_task(
        owner_id="u1", tenant_id="t1", requested_capabilities=["devos.capability.list"],
    )
    with pytest.raises(DelegationError) as ei:
        assert_owner(t, "u1", tenant_id="t2")
    assert ei.value.code == "TENANT_ISOLATION"


def test_plan_is_not_evidence():
    t = delegate_agent_task(owner_id="u1", requested_capabilities=["devos.capability.list"])
    record_plan(t, {"steps": ["read file"], "claim": "done"})
    assert t.plan is not None
    assert t.result.get("plan_is_not_evidence") is True
    assert reject_fabricated_evidence("done")
    assert reject_fabricated_evidence("verified")


def test_fabricated_evidence_ignored_on_complete():
    t = delegate_agent_task(owner_id="u1", requested_capabilities=["devos.capability.list"])
    mark_completed(t, result={"claim": "done"})
    assert t.result.get("claim_ignored") is True
    assert "claim" not in t.result


def test_unknown_no_auto_retry():
    t = delegate_agent_task(owner_id="u1", requested_capabilities=["devos.capability.list"])
    mark_unknown(t, reason="UNKNOWN_SIDE_EFFECT")
    assert t.status == AgentTaskLifecycle.UNKNOWN
    assert t.recovery.get("auto_retry") is False
    with pytest.raises(DelegationError):
        mark_completed(t, result={"ok": True})


def test_restart_after_success_no_duplicate():
    key = "restart-ok"
    a = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"], idempotency_key=key,
    )
    mark_completed(a, result={"x": 1}, evidence_refs=["ev-1"])
    b = delegate_agent_task(
        owner_id="u1", requested_capabilities=["devos.capability.list"], idempotency_key=key,
    )
    assert b.task_id == a.task_id
    assert b.status == AgentTaskLifecycle.COMPLETED


def test_cancel():
    t = delegate_agent_task(owner_id="u1", requested_capabilities=["devos.capability.list"])
    cancel_task(t)
    assert t.status == AgentTaskLifecycle.CANCELLED


@pytest.mark.asyncio
async def test_capability_not_delegated_denied():
    t = delegate_agent_task(owner_id="u1", requested_capabilities=["devos.capability.list"])
    rec = await request_capability(t, capability_id="filesystem.write", execute=False)
    assert rec.status == "denied"


@pytest.mark.asyncio
async def test_agent_step_success():
    from brain.workflow_store import build_execution_snapshot
    steps = [
        WorkflowStep(
            id="A",
            type=StepType.AGENT,
            name="agent",
            inputs={
                "capabilities": ["devos.capability.list"],
                "execute_capabilities": ["devos.capability.list"],
            },
        ),
    ]
    definition = {
        "workflow_id": "wf-agent-1",
        "name": "agent-wf",
        "version": "1.0.0",
        "start_step": "A",
        "steps": [s.to_dict() for s in steps],
        "triggers": ["manual"],
    }
    snap = build_execution_snapshot(
        workflow_id="wf-agent-1",
        workflow_version=1,
        owner_id="u1",
        tenant_id="t1",
        name="agent-wf",
        definition=definition,
        enabled=True,
    )
    result = await run_from_snapshot(
        snap,
        max_steps=1,
        extra_context={"owner_id": "u1", "tenant_id": "t1"},
    )
    assert result.steps
    assert result.steps[0].get("status") in (STEP_SUCCEEDED, "succeeded")


@pytest.mark.asyncio
async def test_agent_step_denies_client_grants():
    body = await execute_agent_step_body(
        {"capabilities": ["devos.capability.list"], "client_supplied_grants": True},
        owner_id="u1",
    )
    assert body["status"] == "failed"
    assert "client_supplied" in (body.get("error") or "").lower() or body.get("error_code") == "AUTHORIZATION_FAILURE"


@pytest.mark.asyncio
async def test_agent_parallel_with_transform():
    """B=AGENT, C=TRANSFORM after A; D joins ALL_SUCCESS."""
    steps = [
        WorkflowStep(id="A", type=StepType.TRANSFORM, name="a", inputs={"expr": "1"}),
        WorkflowStep(
            id="B", type=StepType.AGENT, name="agent",
            inputs={"capabilities": ["devos.capability.list"]},
        ),
        WorkflowStep(id="C", type=StepType.TRANSFORM, name="c", inputs={"expr": "3"}),
        WorkflowStep(
            id="D", type=StepType.TRANSFORM, name="d", inputs={"expr": "4"},
            metadata={"depends_on": ["B", "C"], "join": "all_success"},
        ),
    ]
    definition = {
        "workflow_id": "wf-ag-par",
        "name": "par",
        "version": "1.0.0",
        "start_step": "A",
        "steps": [s.to_dict() for s in steps],
        "edges": [
            {"source": "A", "target": "B", "on": "success"},
            {"source": "A", "target": "C", "on": "success"},
            {"source": "B", "target": "D", "on": "success"},
            {"source": "C", "target": "D", "on": "success"},
        ],
        "joins": [{"target": "D", "deps": ["B", "C"], "mode": "all_success"}],
        "triggers": ["manual"],
    }
    snap = build_execution_snapshot(
        workflow_id="wf-ag-par",
        workflow_version=1,
        owner_id="u1",
        tenant_id="t1",
        name="par",
        definition=definition,
        enabled=True,
    )
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    elig = select_eligible_steps(snap, st)
    assert set(elig) == {"B", "C"}
    out = await execute_parallel_graph(
        snap, st, extra_context={"owner_id": "u1", "tenant_id": "t1"},
    )
    assert out.records.get("B", {}).get("status") == STEP_SUCCEEDED
    assert out.records.get("C", {}).get("status") == STEP_SUCCEEDED
    assert out.records.get("D", {}).get("status") == STEP_SUCCEEDED


def test_idempotency_key_stable():
    k1 = agent_task_idempotency_key(
        owner_id="u", tenant_id="t", parent_run_id="r", agent_type="worker", logical_key="L",
    )
    k2 = agent_task_idempotency_key(
        owner_id="u", tenant_id="t", parent_run_id="r", agent_type="worker", logical_key="L",
    )
    assert k1 == k2
