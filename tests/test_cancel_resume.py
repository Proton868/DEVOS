import asyncio
import pytest

from execution.cancel_cascade import (
    bind_delivery, request_delivery_cancel, is_delivery_cancelled,
    cascade_cancel_plan, clear_delivery_cancel,
)
from execution.durable_resume import reconcile_runtime_records, reconcile_plan_nodes
from execution.durable_store import upsert_runtime, init_store, new_id
from execution.files import FileService
from execution.artifacts import write_bytes
from execution.delivery_executor import execute_delivery_plan


class FakeNode:
    def __init__(self, id, status, type="build"):
        self.id = id
        self.status = status
        self.type = type


class FakePlan:
    def __init__(self):
        self.id = "plan-test-1"
        self.user_id = "u1"
        self.workspace_id = "ws1"
        self.status = "running"
        self.nodes = [
            FakeNode("a", "completed"),
            FakeNode("b", "running"),
            FakeNode("c", "queued"),
            FakeNode("d", "running", type="deploy"),
        ]
        self.agent_task_ids = []
        self.events = []

    def emit(self, name, data=None):
        self.events.append((name, data))


def test_cascade_cancels_nodes_and_flags():
    plan = FakePlan()
    bind_delivery(plan.id, user_id=plan.user_id, project_id=plan.workspace_id)
    ev = asyncio.run(cascade_cancel_plan(plan))
    assert is_delivery_cancelled(plan.id)
    assert plan.status == "cancelled"
    statuses = {n.id: n.status for n in plan.nodes}
    assert statuses["b"] == "cancelled"
    assert statuses["c"] == "cancelled"
    assert statuses["a"] == "completed"
    assert any(e[0] == "orchestration.cancelled" for e in plan.events)
    clear_delivery_cancel(plan.id)


def test_reconcile_runtime_stale_pid():
    init_store()
    rid = new_id("rt_")
    upsert_runtime({
        "runtime_id": rid, "user_id": "u", "project_id": "proj-stale",
        "status": "READY", "pid": 99999999,
    })
    report = reconcile_runtime_records("proj-stale")
    assert any(r.get("status") == "STALE" for r in report)


def test_reconcile_plan_nodes_protects_external():
    plan = FakePlan()
    report = reconcile_plan_nodes(plan)
    assert any(x["id"] == "b" for x in report["reset"])
    assert any(x["id"] == "d" for x in report["external_protected"])
    assert plan.nodes[3].status == "pending_review"


def test_delivery_executor_honors_cancel_flag():
    fs = FileService("cu", "cp")
    write_bytes(fs, "index.html", b"<html>c</html>")
    plan_id = "cancel-exec-1"
    request_delivery_cancel(plan_id)
    result = asyncio.run(execute_delivery_plan(
        user_id="cu", project_id="cp", goal="preview", plan_id=plan_id,
    ))
    assert result["status"] == "cancelled" or any(
        e.get("status") == "cancelled" for e in result["evidence"]
    )
    clear_delivery_cancel(plan_id)


def test_cascade_idempotent():
    plan = FakePlan()
    bind_delivery(plan.id, user_id="u1", project_id="ws1")
    asyncio.run(cascade_cancel_plan(plan))
    asyncio.run(cascade_cancel_plan(plan))
    assert plan.status == "cancelled"
    clear_delivery_cancel(plan.id)
