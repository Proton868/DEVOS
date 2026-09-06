"""Final Phase 4 gate tests — identity, nested, protocol path."""
from __future__ import annotations
from pathlib import Path
import inspect
from core.task_contract import TaskRequest, TaskResult, TaskStatus
from core.task_registry import TaskRegistry
from core.event_store import EventStore, ProtocolEvent, ProtocolEventType
from core.agent_protocol import AgentProtocol, AgentRegistry, AgentDescriptor
from core.task_orch import OrchestrationService
from workers.runtime import WorkerRuntime
from core.loop import BrainExecutionLoop

def test_worker_runtime_accepts_task_param():
    sig = inspect.signature(WorkerRuntime.run)
    assert "task" in sig.parameters

def test_cancel_loop_exists():
    assert hasattr(BrainExecutionLoop, "cancel_loop")

def test_identity_preserved_in_child_request():
    parent = TaskRequest(objective="parent", worker_slug="architect", execution_id="exec-root")
    child = parent.child("coder", "implement")
    assert child.parent_task_id == parent.task_id
    assert child.execution_id == parent.execution_id
    assert child.task_id != parent.task_id
    assert child.root_loop_id == parent.task_id or child.root_loop_id == parent.root_loop_id

def test_nested_tree_and_cancel(tmp_path):
    reg = TaskRegistry(root=tmp_path)
    orch = OrchestrationService(registry=reg, events=EventStore(root=tmp_path / "ev"))
    root = TaskRequest(objective="r", worker_slug="a")
    reg.save_request(root)
    c = root.child("b", "c"); reg.save_request(c); reg.link_child(root.task_id, c.task_id)
    g = c.child("d", "g"); reg.save_request(g); reg.link_child(c.task_id, g.task_id)
    tree = reg.tree(root.task_id)
    assert tree["children"][0]["task_id"] == c.task_id
    assert tree["children"][0]["children"][0]["task_id"] == g.task_id
    results = orch.cancel_tree(root.task_id)
    assert {r.task_id for r in results} >= {root.task_id, c.task_id, g.task_id}
    assert all(r.status == TaskStatus.CANCELLED for r in results)

def test_capability_full_subset():
    ar = AgentRegistry()
    ar.register(AgentDescriptor("agent:coder", "coder", "Coder",
                                 capabilities=["code_generation", "code_editing", "testing"]))
    assert ar.select(["code_editing", "testing"]).agent_id == "agent:coder"
    assert ar.discover(["code_editing", "web_research"]) == []

def test_spawn_agent_source_uses_protocol():
    src = Path("core/loop.py").read_text()
    assert "AgentProtocol" in src
    assert "spawn_agent" in src
    # Must not call WorkerRuntime directly in spawn path
    # (WorkerRuntime may still appear elsewhere)
    spawn_idx = src.find('if action == "spawn_agent"')
    next_action = src.find('if action == "graph_remember"', spawn_idx)
    block = src[spawn_idx:next_action]
    assert "AgentProtocol" in block
    assert "protocol.dispatch" in block

def test_mission_runtime_creates_task_request():
    src = Path("brain/orchestration_runtime.py").read_text()
    assert "TaskRequest" in src
    assert "TaskRegistry" in src
    assert "_finalize_node_task" in src

def test_agent_protocol_dispatch_passes_task():
    src = Path("core/agent_protocol.py").read_text()
    assert "task=request" in src or "task=request," in src
