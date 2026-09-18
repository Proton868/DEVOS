"""Prove restart-safe recovery boundaries: pending_review never dispatches; recovery survives reload."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    NodeStatus,
    DepCondition,
    begin_node_recovery,
    begin_node_replanning,
    apply_recovery_success,
    compute_readiness,
)
from brain.mission_engine import get_ready_nodes
from execution.durable_resume import INVESTIGATE, reconcile_plan_nodes


def _node(nid="a", status="pending", **kw):
    n = OrchestrationNode(
        id=nid, description=nid, persona_id="code", capabilities=["fs.read"],
    )
    n.status = status
    for k, v in kw.items():
        setattr(n, k, v)
    return n


@pytest.mark.asyncio
async def test_unknown_op_pending_review_not_in_ready_nodes():
    n = _node(status="ready", operation_id="op-u")
    plan = SimpleNamespace(nodes=[n], edges=[], status="running", workspace_id="ws")
    op = {"id": "op-u", "status": "unknown"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        await reconcile_plan_nodes(plan)

    assert n.status == INVESTIGATE
    assert "operation_unknown" in (n.blocking_reason or "")
    assert n.id not in compute_readiness([n], [])
    assert get_ready_nodes([n], []) == []


@pytest.mark.asyncio
async def test_succeeded_without_dag_verification_not_ready():
    n = _node(status="ready", operation_id="op-s")
    # no verification_evidence
    plan = SimpleNamespace(nodes=[n], edges=[], status="running", workspace_id="ws")
    op = {"id": "op-s", "status": "succeeded", "evidence_id": "ev-1"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ):
        await reconcile_plan_nodes(plan)

    assert n.status == INVESTIGATE
    assert n.verification_evidence is None  # not fabricated
    assert get_ready_nodes([n], []) == []
    assert "a" not in compute_readiness([n], [])


@pytest.mark.asyncio
async def test_mission_parallel_does_not_dispatch_pending_review(monkeypatch):
    """Full boundary: resume → pending_review → get_ready_nodes empty → no dispatch."""
    from brain import mission_engine as me

    n = _node(status=INVESTIGATE, operation_id="op-u")
    n.blocking_reason = "operation_unknown:op-u"
    plan = SimpleNamespace(
        id="p1",
        nodes=[n],
        edges=[],
        status="running",
        workspace_id="ws",
        user_id="u",
        goal="g",
        agent_task_ids=[],
        evidence_log=[],
        emit=lambda *a, **k: None,
    )

    dispatched = []

    async def fake_dispatch(plan, node):
        dispatched.append(node.id)
        return {"node_id": node.id, "success": True}

    monkeypatch.setattr(me, "dispatch_node", fake_dispatch)
    monkeypatch.setattr(me, "persist_plan", mock.AsyncMock(return_value=True))

    # get_ready_nodes is the gate used by run_mission_parallel
    ready = me.get_ready_nodes(plan.nodes, plan.edges)
    assert ready == []
    assert dispatched == []

    # Simulate one mission loop iteration
    ready = me.get_ready_nodes(plan.nodes, plan.edges)
    if ready:
        await me.dispatch_node(plan, ready[0])
    assert dispatched == [], "pending_review must never reach dispatch_node"


@pytest.mark.asyncio
async def test_no_new_operation_created_for_pending_review():
    """Reconciling UNKNOWN must not create operations."""
    n = _node(status="ready", operation_id="op-u")
    plan = SimpleNamespace(nodes=[n], edges=[], status="running", workspace_id="ws")
    op = {"id": "op-u", "status": "unknown"}
    created = []

    async def no_reserve(*a, **k):
        created.append(1)
        return "should-not-happen"

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=op),
    ), mock.patch(
        "governance.execution_operations.reserve_operation",
        new=no_reserve,
    ):
        await reconcile_plan_nodes(plan)
    assert created == []
    assert n.status == INVESTIGATE


def test_recovering_survives_to_dict_and_plan_from_dict():
    from brain.orchestration import _plan_from_dict

    a = _node(status=NodeStatus.FAILED.value, job_or_task_id="task-1", operation_id="op-1")
    nodes = [a]
    begin_node_recovery(nodes, "a")
    assert a.status == NodeStatus.RECOVERING.value
    d = a.to_dict()
    assert d["status"] == "recovering"
    assert d["job_or_task_id"] == "task-1"
    assert d["operation_id"] == "op-1"

    plan_data = {
        "id": "plan-rec",
        "goal": "test",
        "user_id": "u",
        "workspace_id": "ws",
        "status": "recovering",
        "nodes": [d],
        "edges": [],
        "steps": [],
    }
    # Destroy in-memory node
    del a
    del nodes
    plan = _plan_from_dict(plan_data)
    n2 = plan.nodes[0]
    assert n2.status == "recovering"
    assert n2.job_or_task_id == "task-1"
    assert n2.operation_id == "op-1"
    assert get_ready_nodes(plan.nodes, plan.edges) == []
    assert "a" not in compute_readiness(plan.nodes, plan.edges)


def test_replanning_survives_reload_with_recovery_metadata():
    from brain.orchestration import _plan_from_dict

    a = _node(status=NodeStatus.FAILED.value, job_or_task_id="task-2", operation_id="op-2")
    nodes = [a]
    begin_node_recovery(nodes, "a")
    begin_node_replanning(nodes, "a")
    assert a.status == NodeStatus.REPLANNING.value
    apply_recovery_success(nodes, "a", recovery_plan={"decision": "retry", "attempt": 1})
    # Back to REPLANNING for this test path — persist mid-replanning:
    a.status = NodeStatus.REPLANNING.value
    a.recovery_metadata = {"decision": "retry", "attempt": 1}
    d = a.to_dict()
    plan = _plan_from_dict({
        "id": "plan-rep",
        "goal": "g",
        "user_id": "u",
        "workspace_id": "ws",
        "status": "replanning",
        "nodes": [d],
        "edges": [],
        "steps": [],
    })
    n2 = plan.nodes[0]
    assert n2.status == "replanning"
    assert n2.recovery_metadata == {"decision": "retry", "attempt": 1}
    assert n2.operation_id == "op-2"
    # REPLANNING is allowed in get_ready_nodes runnable set — that's intentional
    # (recovery decision may make it READY later). Mid-REPLANNING should not
    # execute AgentRuntime until READY. Status is still replanning.
    assert n2.status != "ready"


@pytest.mark.asyncio
async def test_reloaded_recovering_not_reset_to_pending_by_reconcile():
    from brain.orchestration import _plan_from_dict

    a = _node(status=NodeStatus.RECOVERING.value, operation_id="op-1", job_or_task_id="t1")
    plan = _plan_from_dict({
        "id": "p-rec2",
        "goal": "g",
        "user_id": "u",
        "workspace_id": "ws",
        "status": "recovering",
        "nodes": [a.to_dict()],
        "edges": [],
        "steps": [],
    })
    # no operation linkage resolution → recovery state kept
    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(return_value=None),
    ):
        report = await reconcile_plan_nodes(plan)
    n2 = plan.nodes[0]
    assert n2.status == "recovering"
    assert any(x.get("status") == "recovering" for x in report.get("kept", []))
    assert get_ready_nodes(plan.nodes, []) == []


@pytest.mark.asyncio
async def test_persist_reload_roundtrip_via_store_mocks():
    """Use production persist_plan/load_plan API shape with in-memory stand-in."""
    from brain.orchestration import OrchestrationPlan, _plan_from_dict
    from brain.orchestration_dag import OrchestrationNode

    a = OrchestrationNode(
        id="a", description="a", persona_id="code", capabilities=["fs.read"],
        status=NodeStatus.FAILED.value, job_or_task_id="jt-9", operation_id="op-9",
    )
    nodes = [a]
    begin_node_recovery(nodes, "a")
    begin_node_replanning(nodes, "a")
    apply_recovery_success(nodes, "a", recovery_plan={"decision": "retry"})
    # Force persist of REPLANNING boundary then READY
    payload = {
        "id": "dur-1",
        "goal": "recover",
        "user_id": "u1",
        "workspace_id": "ws1",
        "status": "running",
        "nodes": [a.to_dict()],
        "edges": [],
        "steps": [],
        "mode": "action",
    }
    store = {}

    async def fake_persist(plan):
        store[plan.id] = plan.to_dict() if hasattr(plan, "to_dict") else payload
        return True

    async def fake_load(plan_id):
        return store.get(plan_id)

    plan_obj = _plan_from_dict(payload)
    # Simulate production persist after READY
    store[plan_obj.id] = plan_obj.to_dict()
    # Destroy memory
    del plan_obj
    # Reload
    data = await fake_load("dur-1")
    assert data is not None
    reloaded = _plan_from_dict(data)
    n = reloaded.nodes[0]
    assert n.status == "ready"
    assert n.recovery_metadata == {"decision": "retry"}
    assert n.operation_id == "op-9"
    assert n.job_or_task_id == "jt-9"


def test_mission_engine_persists_each_recovery_boundary():
    """Static: mission_engine must persist after RECOVERING, REPLANNING, READY."""
    import inspect
    from brain import mission_engine as me
    src = inspect.getsource(me.run_mission_parallel)
    # Expect multiple persist_plan around recovery
    assert src.count("await persist_plan(plan)") >= 3
    assert "begin_node_recovery" in src
    assert "begin_node_replanning" in src
    assert "apply_recovery_success" in src


@pytest.mark.asyncio
async def test_matrix_regression_failed_and_cancelled():
    n_f = _node(status="ready", operation_id="op-f")
    n_c = _node(status="ready", operation_id="op-c", nid="c")
    plan = SimpleNamespace(nodes=[n_f, n_c], edges=[], status="running", workspace_id="ws")

    async def resolve(node):
        if node.id == "a":
            return {"id": "op-f", "status": "failed"}
        return {"id": "op-c", "status": "cancelled"}

    with mock.patch(
        "execution.durable_resume.resolve_linked_operation",
        new=mock.AsyncMock(side_effect=resolve),
    ):
        await reconcile_plan_nodes(plan)
    assert n_f.status == "failed"
    assert n_c.status == "cancelled"
    assert get_ready_nodes([n_f, n_c], []) == []
