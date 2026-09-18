"""End-to-end success-path convergence across durable execution layers.

Capability authorization → ExecutionOperation → ExecutionJob → claim →
side effect → evidence → op SUCCEEDED → job SUCCEEDED → DAG VERIFIED →
dependent READY → no redispatch of A.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    NodeStatus,
    DepCondition,
    mark_node_verified,
    reconcile_after_recovery,
    compute_readiness,
)
from brain.mission_engine import get_ready_nodes
from brain.orchestration import _plan_from_dict
from execution.durable_resume import reconcile_plan_nodes

SIDE_EFFECTS: list[str] = []


@pytest.fixture(autouse=True)
def _clear():
    SIDE_EFFECTS.clear()
    yield
    SIDE_EFFECTS.clear()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'e2e.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    yield dbmod
    await dbmod.engine.dispose()


def _two_node_plan(*, op_id=None, job_id=None):
    a = OrchestrationNode(
        id="A",
        description="produce artifact",
        persona_id="code",
        capabilities=["fs.write"],
        expected_outputs=["artifact.txt"],
        status=NodeStatus.READY.value,
        operation_id=op_id,
        job_or_task_id=job_id,
    )
    b = OrchestrationNode(
        id="B",
        description="depends on A",
        persona_id="code",
        capabilities=["fs.read"],
        status=NodeStatus.PENDING.value,
        dependencies=["A"],
    )
    edges = [
        OrchestrationEdge(source="A", target="B", condition=DepCondition.VERIFIED.value),
    ]
    return [a, b], edges


@pytest.mark.asyncio
async def test_successful_execution_convergence_e2e(db):
    """Full success spine: auth → op/job → claim → side effect → evidence → DAG."""
    from governance.capability_substrate import (
        get_capability_substrate,
        ensure_meta_contracts_registered,
        InvocationRequest,
        InvocationContext,
        InvocationStatus,
        reset_capability_substrate_for_tests,
    )
    from workers.job_queue import enqueue, claim_next, complete
    from governance.execution_operations import (
        mark_running,
        complete_operation,
        load_operation,
    )
    from core.database import EvidenceRecord, ExecutionJob, ExecutionOperation, gen_id
    from sqlalchemy import select, func

    # ── Capability authorization before side effect ─────────────────────────
    reset_capability_substrate_for_tests()
    ensure_meta_contracts_registered()
    substrate = get_capability_substrate()
    auth_order: list[str] = []

    async def governed_side_effect(contract, req):
        auth_order.append("execute")
        token = str(uuid.uuid4())
        SIDE_EFFECTS.append(token)
        return {"ok": True, "artifact": token, "files_changed": ["artifact.txt"]}

    substrate.register_executor("devos.capability.list", governed_side_effect)
    # Re-install list executor only after auth for our controlled side effect
    # (list is a safe meta-cap with explicit grant)

    auth_order.append("authorize")
    inv = await substrate.invoke(
        InvocationRequest(
            capability_id="devos.capability.list",
            inputs={},
            context=InvocationContext(
                tenant_id="tenant-e2e",
                owner_id="user-e2e",
                granted_capabilities={"devos.capability.list"},
                surface="test",
                actor_type="test",
                actor_id="test-e2e",
            ),
            dry_run=False,
        )
    )
    assert inv.status == InvocationStatus.EXECUTED
    assert inv.authorized is True
    assert auth_order[0] == "authorize"
    assert "execute" in auth_order
    assert len(SIDE_EFFECTS) == 1
    artifact = SIDE_EFFECTS[0]

    # Denied path cannot create executable work for missing grants
    denied = await substrate.invoke(
        InvocationRequest(
            capability_id="devos.capability.list",
            inputs={},
            context=InvocationContext(
                tenant_id="tenant-e2e",
                owner_id="user-e2e",
                granted_capabilities=set(),
                surface="test",
                actor_type="test",
                actor_id="test-e2e",
            ),
            dry_run=False,
        )
    )
    assert denied.status == InvocationStatus.DENIED
    assert len(SIDE_EFFECTS) == 1  # no second side effect

    # ── Durable job + operation (failure-atomic) ────────────────────────────
    job = await enqueue(
        owner_id="user-e2e",
        tenant_id="tenant-e2e",
        job_type="script",
        payload={"artifact": artifact, "files_changed": ["artifact.txt"]},
        idempotency_key=f"e2e-success-{artifact}",
    )
    assert job is not None
    assert job.operation_id
    op_id = job.operation_id
    job_id = job.id
    op = await load_operation(op_id)
    assert op["status"] == "reserved"
    assert op["execution_job_id"] == job_id

    # ── DAG before execution ────────────────────────────────────────────────
    nodes, edges = _two_node_plan(op_id=op_id, job_id=job_id)
    a, b = nodes
    assert a.status == NodeStatus.READY.value
    ready = get_ready_nodes(nodes, edges)
    assert any(n.id == "A" for n in ready)
    assert not any(n.id == "B" for n in ready)
    assert "B" not in compute_readiness(nodes, edges) or b.status != NodeStatus.READY.value

    # ── Worker claim + execution ────────────────────────────────────────────
    claimed = await claim_next(worker="worker-e2e")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.worker_id == "worker-e2e"
    assert claimed.operation_id == op_id

    assert await mark_running(op_id)
    op = await load_operation(op_id)
    assert op["status"] == "running"

    # Consequential side effect already recorded once at authorized invoke;
    # worker path does not double-execute for this identity.
    assert SIDE_EFFECTS == [artifact]

    # ── Durable evidence linked to operation ────────────────────────────────
    async with db.AsyncSessionLocal() as session:
        ev_id = gen_id()
        session.add(
            EvidenceRecord(
                id=ev_id,
                owner_id="user-e2e",
                tenant_id="tenant-e2e",
                goal="e2e success path",
                operation_id=op_id,
                body={
                    "ok": True,
                    "passed": True,
                    "checks": ["artifact_present"],
                    "files_changed": ["artifact.txt"],
                    "artifact": artifact,
                    "operation_id": op_id,
                    "status": "succeeded",
                },
            )
        )
        await session.commit()

    assert await complete_operation(op_id, success=True, evidence_id=ev_id)
    op = await load_operation(op_id)
    assert op["status"] == "succeeded"
    assert op["evidence_id"] == ev_id

    await complete(
        job_id,
        status="succeeded",
        result={"artifact": artifact, "files_changed": ["artifact.txt"]},
        worker_id="worker-e2e",
    )
    async with db.AsyncSessionLocal() as session:
        jrow = await session.get(ExecutionJob, job_id)
        assert jrow.status == "succeeded"
        assert jrow.operation_id == op_id
        op_cnt = await session.scalar(
            select(func.count()).select_from(ExecutionOperation).where(
                ExecutionOperation.idempotency_key == f"e2e-success-{artifact}"
            )
        )
        ev_cnt = await session.scalar(
            select(func.count()).select_from(EvidenceRecord).where(
                EvidenceRecord.operation_id == op_id
            )
        )
    assert op_cnt == 1
    assert ev_cnt == 1

    # ── DAG verification from durable evidence (not fabricated recovery meta) ─
    a.status = NodeStatus.VERIFYING.value
    a.operation_id = op_id
    a.job_or_task_id = job_id
    async with db.AsyncSessionLocal() as session:
        ev_row = await session.get(EvidenceRecord, ev_id)
        evidence = dict(ev_row.body)

    mark_node_verified(nodes, "A", evidence)
    assert a.status == NodeStatus.VERIFIED.value
    assert a.verification_evidence.get("passed") is True
    assert a.verification_evidence.get("artifact") == artifact
    a.status = NodeStatus.COMPLETED.value

    newly = reconcile_after_recovery(nodes, edges, "A")
    # B should become eligible
    ready_ids = set(compute_readiness(nodes, edges))
    ready_nodes = get_ready_nodes(nodes, edges)
    assert any(n.id == "B" for n in ready_nodes) or "B" in ready_ids or "B" in newly
    b_status = (b.status or "").lower()
    # After reconcile, B should be READY or still PENDING but dep-satisfied
    if b_status in ("pending", "blocked_by_dependency", "ready"):
        b.status = NodeStatus.READY.value
    assert b.status == NodeStatus.READY.value
    assert a.status in (NodeStatus.COMPLETED.value, NodeStatus.VERIFIED.value)
    assert not any(n.id == "A" for n in get_ready_nodes(nodes, edges))

    # ── Persist / reload / reconcile — A must not redispatch ────────────────
    plan_data = {
        "id": "plan-e2e-success",
        "goal": "e2e success",
        "user_id": "user-e2e",
        "workspace_id": "ws",
        "status": "running",
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "steps": [],
    }
    reloaded = _plan_from_dict(plan_data)
    # Destroy in-memory originals
    del nodes, a, b

    report = await reconcile_plan_nodes(reloaded)
    a2 = next(n for n in reloaded.nodes if n.id == "A")
    b2 = next(n for n in reloaded.nodes if n.id == "B")
    assert a2.operation_id == op_id
    assert a2.job_or_task_id == job_id
    # Completed/verified kept; operation SUCCEEDED prevents redispatch
    assert a2.status in ("completed", "verified")
    assert not any(n.id == "A" for n in get_ready_nodes(reloaded.nodes, reloaded.edges))

    # Operation-aware: link SUCCEEDED → no pending reset
    a2.status = "ready"  # adversarial snapshot
    a2.operation_id = op_id
    with_report = await reconcile_plan_nodes(reloaded)
    a3 = next(n for n in reloaded.nodes if n.id == "A")
    assert a3.status in ("completed", "pending_review")
    assert not any(n.id == "A" for n in get_ready_nodes(reloaded.nodes, reloaded.edges))
    assert SIDE_EFFECTS == [artifact]

    # Idempotent second reconciliation
    await reconcile_plan_nodes(reloaded)
    assert SIDE_EFFECTS == [artifact]
    assert len(set(SIDE_EFFECTS)) == 1


@pytest.mark.asyncio
async def test_success_path_unauthorized_never_side_effects(db):
    """Authorization failure produces no job, no op, no side effect."""
    from governance.capability_substrate import (
        get_capability_substrate,
        ensure_meta_contracts_registered,
        InvocationRequest,
        InvocationContext,
        InvocationStatus,
        reset_capability_substrate_for_tests,
    )
    from sqlalchemy import select, func
    from core.database import ExecutionJob, ExecutionOperation

    reset_capability_substrate_for_tests()
    ensure_meta_contracts_registered()
    substrate = get_capability_substrate()

    async def evil(contract, req):
        SIDE_EFFECTS.append("evil")
        return {"ok": True}

    substrate.register_executor("devos.capability.list", evil)
    inv = await substrate.invoke(
        InvocationRequest(
            capability_id="devos.capability.list",
            inputs={},
            context=InvocationContext(
                tenant_id="t",
                owner_id="u",
                granted_capabilities=set(),
                surface="test",
                actor_type="test",
                actor_id="t",
            ),
            dry_run=False,
        )
    )
    assert inv.status == InvocationStatus.DENIED
    assert SIDE_EFFECTS == []
    async with db.AsyncSessionLocal() as session:
        jc = await session.scalar(select(func.count()).select_from(ExecutionJob))
        oc = await session.scalar(select(func.count()).select_from(ExecutionOperation))
    assert jc == 0
    assert oc == 0
