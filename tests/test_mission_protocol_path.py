"""Prove mission nodes go through AgentProtocol, not AgentRuntime."""
from __future__ import annotations
import asyncio
from pathlib import Path
from unittest.mock import patch

from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
from core.task_contract import TaskRequest, TaskResult, TaskStatus


def test_source_has_no_agent_runtime_in_run_node():
    src = Path("brain/orchestration_runtime.py").read_text()
    start = src.find("async def run_node_on_agent_runtime")
    end = src.find("async def _fake_runtime")
    block = src[start:end]
    assert "AgentRuntime(" not in block
    assert "_run_node_via_agent_protocol" in block
    assert "AgentProtocol" in src


def test_chat_does_not_call_mirror():
    src = Path("api/routes/chat.py").read_text()
    assert "mirror_durable_task" not in src


def test_mission_node_invokes_agent_protocol_dispatch():
    """Fail if mission executor bypasses AgentProtocol."""
    called = {}

    async def fake_dispatch(self, request, requester_identity, provider=None, model=None, on_step=None, agent=None):
        called["task_id"] = request.task_id
        called["worker_slug"] = request.worker_slug
        called["dispatch"] = True
        return TaskResult(
            task_id=request.task_id,
            execution_id=request.execution_id,
            worker_slug=request.worker_slug,
            status=TaskStatus.SUCCEEDED,
            output="ok-from-protocol",
            attempt=request.attempt,
            root_loop_id=request.root_loop_id,
        )

    req = NodeExecutionRequest(
        plan_id="plan-1",
        node_id="node-a",
        user_id="user-1",
        workspace_id="ws-1",
        persona_id="fullstack-engineer",
        objective="build a small module",
        effective_caps=["ucip:filesystem.write"],
        authorization_decision="allow",
    )

    async def _run():
        with patch("core.agent_protocol.AgentProtocol.dispatch", new=fake_dispatch):
            return await run_node_on_agent_runtime(req)

    result = asyncio.run(_run())

    assert called.get("dispatch") is True
    assert called["task_id"] == "node:plan-1:node-a"
    assert called["worker_slug"] == "fullstack-engineer"
    assert result.success is True
    assert result.task_id == "node:plan-1:node-a"
    assert "ok-from-protocol" in (result.summary or "")


def test_nested_identity_contract():
    parent = TaskRequest(objective="p", worker_slug="architect")
    child = parent.child("coder", "impl")
    assert child.parent_task_id == parent.task_id
    assert child.task_id != parent.task_id
    assert child.execution_id == parent.execution_id
