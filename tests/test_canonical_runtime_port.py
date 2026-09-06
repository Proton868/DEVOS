"""Ported canonical runtime primitives on Proton868 main lineage."""
from pathlib import Path
import tempfile
from core.task_contract import TaskRequest, TaskResult, TaskStatus
from core.task_registry import TaskRegistry
from core.event_store import EventStore, ProtocolEvent, ProtocolEventType
from core.agent_protocol import AgentRegistry, AgentDescriptor
from core.task_orch import OrchestrationService
from core.loop import BrainExecutionLoop

def test_cancel_loop_exists():
    assert hasattr(BrainExecutionLoop, "cancel_loop")

def test_task_roundtrip(tmp_path):
    reg = TaskRegistry(root=tmp_path)
    req = TaskRequest(objective="build", worker_slug="coder")
    reg.save_request(req)
    assert TaskRegistry(root=tmp_path).get_request(req.task_id).objective == "build"

def test_capability_subset():
    ar = AgentRegistry()
    ar.register(AgentDescriptor("agent:coder", "coder", "Coder", capabilities=["code_generation", "testing"]))
    assert ar.discover(["code_generation", "testing"])
    assert ar.discover(["code_generation", "web_research"]) == []

def test_cancel_tree(tmp_path):
    reg = TaskRegistry(root=tmp_path)
    orch = OrchestrationService(registry=reg, events=EventStore(root=tmp_path / "ev"))
    root = TaskRequest(objective="r", worker_slug="a")
    reg.save_request(root)
    c = root.child("b", "c"); reg.save_request(c); reg.link_child(root.task_id, c.task_id)
    results = orch.cancel_tree(root.task_id)
    assert all(r.status == TaskStatus.CANCELLED for r in results)

def test_event_persist(tmp_path):
    store = EventStore(root=tmp_path)
    store.emit(ProtocolEvent(ProtocolEventType.TASK_STARTED, "t1", "e1"))
    assert EventStore(root=tmp_path).for_task("t1")[0].task_id == "t1"
