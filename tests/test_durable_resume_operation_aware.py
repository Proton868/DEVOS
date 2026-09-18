"""Operation-aware orchestration resume — consult ExecutionOperation before re-dispatch."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from brain.orchestration_dag import (
    OrchestrationNode,
    NodeStatus,
    begin_node_recovery,
    begin_node_replanning,
    apply_recovery_success,
)


def _node(nid="a", status="ready", job=None, op=None):
    n = OrchestrationNode(
        id=nid, description=nid, persona_id="code", capabilities=["fs.read"],
    )
    n.status = status
    n.job_or_task_id = job
    n.operation_id = op
    return n


def _plan(nodes):
    return SimpleNamespace(nodes=nodes, edges=[], status="running", workspace_id="ws")


@pytest.mark.asyncio
async def test_succeeded_operation_not_redispatched():
    from execution.durable_resume import reconcile_plan_nodes

    n = _node(status="ready", op="op-1")
    plan = _plan([n])
    op = {"id": "op-1", "status": "succeeded", "evidence_id": "ev-1"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        report = await reconcile_plan_nodes(plan)
    assert n.status != "pending"
    assert n.status in ("completed", "pending_review")
    assert any(x["id"] == "a" for x in report.get("op_succeeded", []))


@pytest.mark.asyncio
async def test_unknown_operation_not_executed():
    from execution.durable_resume import reconcile_plan_nodes, INVESTIGATE

    n = _node(status="pending", op="op-u")
    plan = _plan([n])
    op = {"id": "op-u", "status": "unknown"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        report = await reconcile_plan_nodes(plan)
    assert n.status == INVESTIGATE
    assert "operation_unknown" in (n.blocking_reason or "")
    assert report["op_unknown"]


@pytest.mark.asyncio
async def test_running_operation_reconciled_not_duplicated():
    from execution.durable_resume import reconcile_plan_nodes, INVESTIGATE

    n = _node(status="running", op="op-r")
    plan = _plan([n])
    op = {"id": "op-r", "status": "running"}

    async def _recon(oid):
        return {"ok": True, "status": "unknown", "reason_code": "marked_unknown"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ), mock.patch(
        "governance.execution_operations.reconcile_operation",
        new=_recon,
    ):
        report = await reconcile_plan_nodes(plan)
    assert n.status == INVESTIGATE
    assert report.get("op_reconciled_unknown") or report.get("op_running_kept")


@pytest.mark.asyncio
async def test_failed_operation_routes_to_failed():
    from execution.durable_resume import reconcile_plan_nodes

    n = _node(status="ready", op="op-f")
    plan = _plan([n])
    op = {"id": "op-f", "status": "failed", "error": "boom"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        await reconcile_plan_nodes(plan)
    assert n.status == "failed"


@pytest.mark.asyncio
async def test_no_linkage_preserves_reset_behavior():
    from execution.durable_resume import reconcile_plan_nodes

    n = _node(status="running", job=None, op=None)
    plan = _plan([n])

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=None),
    ):
        report = await reconcile_plan_nodes(plan)
    assert n.status == "pending"
    assert any(x["id"] == "a" for x in report["reset"])


@pytest.mark.asyncio
async def test_reserved_operation_safe_pending():
    from execution.durable_resume import reconcile_plan_nodes

    n = _node(status="running", op="op-res")
    plan = _plan([n])
    op = {"id": "op-res", "status": "reserved"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        await reconcile_plan_nodes(plan)
    assert n.status == "pending"


@pytest.mark.asyncio
async def test_idempotent_reconcile():
    from execution.durable_resume import reconcile_plan_nodes, INVESTIGATE

    n = _node(status="ready", op="op-u")
    plan = _plan([n])
    op = {"id": "op-u", "status": "unknown"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        r1 = await reconcile_plan_nodes(plan)
        r2 = await reconcile_plan_nodes(plan)
    assert n.status == INVESTIGATE
    assert r1["op_unknown"] and r2["op_unknown"]


def test_recovery_transitions_survive_to_dict_roundtrip():
    """FAILED→RECOVERING→REPLANNING→READY fields persist via node.to_dict()."""
    from brain.orchestration_dag import OrchestrationNode

    a = OrchestrationNode(id="a", description="a", persona_id="code", capabilities=["fs.read"])
    a.status = NodeStatus.FAILED.value
    a.job_or_task_id = "task-stable"
    nodes = [a]
    begin_node_recovery(nodes, "a")
    assert a.status == NodeStatus.RECOVERING.value
    d1 = a.to_dict()
    assert d1["status"] == "recovering"
    assert d1["job_or_task_id"] == "task-stable"

    begin_node_replanning(nodes, "a")
    d2 = a.to_dict()
    assert d2["status"] == "replanning"

    apply_recovery_success(nodes, "a", recovery_plan={"decision": "retry"})
    d3 = a.to_dict()
    assert d3["status"] == "ready"
    assert d3["recovery_metadata"] == {"decision": "retry"}
    assert d3["job_or_task_id"] == "task-stable"
    # Reload shape used by plan_from_dict
    a2 = OrchestrationNode(
        id=d3["id"], description=d3["description"], persona_id=d3["persona_id"],
        capabilities=d3["capabilities"], status=d3["status"],
        job_or_task_id=d3["job_or_task_id"],
        recovery_metadata=d3.get("recovery_metadata"),
        operation_id=d3.get("operation_id"),
    )
    assert a2.status == "ready"
    assert a2.recovery_metadata == {"decision": "retry"}


@pytest.mark.asyncio
async def test_succeeded_with_verification_evidence_completes():
    from execution.durable_resume import reconcile_plan_nodes

    n = _node(status="ready", op="op-s")
    n.verification_evidence = {"ok": True, "checks": ["unit"]}
    plan = _plan([n])
    op = {"id": "op-s", "status": "succeeded"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        await reconcile_plan_nodes(plan)
    assert n.status == "completed"


@pytest.mark.asyncio
async def test_resolve_uses_operation_id_field():
    from execution.durable_resume import resolve_linked_operation

    n = _node(op="op-direct")
    with mock.patch(
        "governance.execution_operations.load_operation",
        new=mock.AsyncMock(return_value={"id": "op-direct", "status": "succeeded"}),
    ):
        op = await resolve_linked_operation(n)
    assert op and op["id"] == "op-direct"
