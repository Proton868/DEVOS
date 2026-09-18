"""Integration: wire DAG recovery into live failure/re-exec paths (no second executor)."""
from __future__ import annotations

from unittest import mock

import pytest

from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    NodeStatus,
    DepCondition,
    propagate_failure,
    begin_node_recovery,
    begin_node_replanning,
    apply_recovery_success,
    apply_recovery_failure,
    mark_node_verified,
    reconcile_after_recovery,
    compute_readiness,
    has_recovery_path,
)


def test_runtime_failure_blocks_ordinary_dependent():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    # Simulate runtime failure marking FAILED
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "task-1"
    blocked = propagate_failure([a, b], edges, "a")
    assert "b" in blocked
    assert b.status == NodeStatus.BLOCKED_BY_DEPENDENCY.value
    assert a.job_or_task_id == "task-1"


def test_failed_edge_target_not_blocked():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    r = OrchestrationNode(id="r", description="recovery", persona_id="code", capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "r", DepCondition.FAILED.value),
    ]
    a.status = NodeStatus.FAILED.value
    blocked = propagate_failure([a, b, r], edges, "a")
    assert "b" in blocked and "r" not in blocked
    assert has_recovery_path([a, b, r], edges, "a")
    assert "r" in compute_readiness([a, b, r], edges)


def test_recovery_lifecycle_not_verified():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "j1"
    begin_node_recovery([a], "a")
    assert a.status == NodeStatus.RECOVERING.value
    begin_node_replanning([a], "a")
    assert a.status == NodeStatus.REPLANNING.value
    apply_recovery_success([a], "a", recovery_plan={"decision": "retry"})
    assert a.status == NodeStatus.READY.value
    assert a.status != NodeStatus.VERIFIED.value
    assert a.recovery_metadata == {"decision": "retry"}
    assert a.verification_evidence is None
    assert a.job_or_task_id == "j1"


@pytest.mark.asyncio
async def test_reexecution_uses_run_node_on_agent_runtime():
    """Recovered READY node must go through existing runtime adapter."""
    from brain.orchestration_runtime import NodeExecutionRequest, NodeExecutionResult

    called = {}

    async def fake_run(req: NodeExecutionRequest):
        called["req"] = req
        return NodeExecutionResult(
            success=True, status="ok", task_id="task-reexec", files_changed=["x.py"],
        )

    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "task-orig"
    a.authorization_decision = "allow"
    begin_node_recovery([a], "a")
    begin_node_replanning([a], "a")
    apply_recovery_success([a], "a", recovery_plan={"decision": "retry"})
    assert a.status == NodeStatus.READY.value

    with mock.patch("brain.orchestration_runtime.run_node_on_agent_runtime", side_effect=fake_run):
        from brain.orchestration_runtime import run_node_on_agent_runtime
        req = NodeExecutionRequest(
            plan_id="p1",
            node_id=a.id,
            user_id="u",
            workspace_id="ws",
            persona_id=a.persona_id,
            objective="retry",
            effective_caps=list(a.capabilities),
            authorization_decision="allow",
        )
        result = await run_node_on_agent_runtime(req)
    assert result.success is True
    assert called["req"].node_id == "a"
    assert a.job_or_task_id == "task-orig"  # correlation preserved until runtime assigns new id


def test_verification_then_reconcile():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [OrchestrationEdge("a", "b", DepCondition.VERIFIED.value)]
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "j"
    propagate_failure([a, b], edges, "a")
    begin_node_recovery([a], "a")
    begin_node_replanning([a], "a")
    apply_recovery_success([a], "a", recovery_plan={"retry": 1})
    a.status = NodeStatus.VERIFYING.value
    with pytest.raises(ValueError):
        mark_node_verified([a], "a", {})  # recovery metadata is not enough
    mark_node_verified([a], "a", {"ok": True, "checks": ["unit"]})
    assert a.status == NodeStatus.VERIFIED.value
    ready = reconcile_after_recovery([a, b], edges, "a")
    assert "b" in ready
    assert b.status == NodeStatus.READY.value


def test_recovery_failure_no_verified():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    begin_node_recovery([a], "a")
    apply_recovery_failure([a], "a", reason="exhausted")
    assert a.status == NodeStatus.FAILED.value
    assert a.status != NodeStatus.VERIFIED.value


def test_idempotent_recovery_calls():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "stable"
    r1 = begin_node_recovery([a], "a")
    r2 = begin_node_recovery([a], "a")
    assert r1["transitioned"] is True
    assert r2["transitioned"] is False
    assert a.job_or_task_id == "stable"
    begin_node_replanning([a], "a")
    apply_recovery_success([a], "a", recovery_plan={"x": 1})
    apply_recovery_success([a], "a", recovery_plan={"x": 1})
    assert a.status == NodeStatus.READY.value


def test_mission_engine_recovery_imports():
    """mission_engine must import and use canonical recovery APIs."""
    import inspect
    from brain import mission_engine as me
    src = inspect.getsource(me)
    assert "begin_node_recovery" in src
    assert "apply_recovery_success" in src
    assert "reconcile_after_recovery" in src
    assert "mark_node_verified" in src


def test_orchestration_recovery_uses_dag_apis():
    import inspect
    from brain import orchestration as orch
    src = inspect.getsource(orch)
    assert "begin_node_recovery" in src
    assert "apply_recovery_success" in src
    assert "mark_node_verified" in src
    assert "run_node_on_agent_runtime" in src
