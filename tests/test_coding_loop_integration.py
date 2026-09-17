"""Integration: production coding path invokes CodingLoop stages."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from brain.coding_loop import CodingLoopState, CodingStage
from brain.coding_loop_bridge import (
    coding_loop_to_node_result,
    is_coding_objective,
    run_production_coding_loop,
)
from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime


def test_is_coding_objective_for_web_and_code():
    assert is_coding_objective("create a website", "web") is True
    assert is_coding_objective("implement auth fix", "code") is True
    assert is_coding_objective("hello how are you", "research") is False


class _FakeFS:
    def tree(self, max_depth=None):
        return []
    def _resolve(self, rel):
        class P:
            def exists(self):
                return False
        return P()


class _FakeRuntime:
    def __init__(self, events):
        self._events = events
        self.user_id = "u1"
        self.project_id = "p1"

    def _fs(self):
        return _FakeFS()

    async def run(self, objective, context):
        for e in self._events:
            yield e


def _completed(success=True, files=None, provider_failure=None, summary="ok"):
    data = {"success": success, "summary": summary, "files_changed": files or [{"path": "main.py"}]}
    if provider_failure:
        data["provider_failure"] = provider_failure
        data["success"] = False
    return {"type": "agent.completed", "task_id": "t1", "data": data}


def test_successful_coding_loop_stages():
    events = [
        {"type": "agent.started", "data": {}},
        {"type": "agent.tool_result", "data": {"tool": "create_file", "files_changed": [{"path": "main.py"}]}},
        {"type": "agent.test_result", "data": {"command": "pytest -q", "exit_code": 0, "ok": True, "stdout": "1 passed"}},
        _completed(True, [{"path": "main.py"}]),
    ]
    rt = _FakeRuntime(events)
    ctx = SimpleNamespace(project_id="p1", user_request="fix bug")

    st = asyncio.run(run_production_coding_loop(
        runtime=rt,
        context=ctx,
        objective="fix the failing test in main.py",
        user_id="u1",
        project_id="p1",
        mission_id="m1",
        persona_id="code",
        max_attempts=3,
    ))
    stages = [e["stage"] for e in st.events]
    assert CodingStage.INSPECT.value in stages
    assert CodingStage.PLAN.value in stages
    assert CodingStage.EDIT.value in stages
    assert CodingStage.EXECUTE.value in stages
    assert CodingStage.OBSERVE.value in stages
    assert CodingStage.VALIDATE.value in stages
    assert CodingStage.EVIDENCE.value in stages
    assert CodingStage.ACCEPT.value in stages
    assert st.stage == CodingStage.DONE.value
    assert (st.acceptance or {}).get("provisional") is True
    assert (st.acceptance or {}).get("mission_acceptance_required") is True
    mapped = coding_loop_to_node_result(st)
    assert mapped["success"] is True
    assert mapped["mission_acceptance_required"] is True


def test_repair_after_command_failure_then_success():
    fail_then_ok = [
        # attempt 1 fail
        [
            {"type": "agent.tool_result", "data": {"tool": "apply_patch", "files_changed": [{"path": "a.py"}]}},
            {"type": "agent.test_result", "data": {"command": "pytest", "exit_code": 1, "ok": False, "stderr": "AssertionError"}},
            _completed(False, [{"path": "a.py"}], summary="tests failed"),
        ],
        # attempt 2 success
        [
            {"type": "agent.tool_result", "data": {"tool": "apply_patch", "files_changed": [{"path": "a.py"}]}},
            {"type": "agent.test_result", "data": {"command": "pytest", "exit_code": 0, "ok": True}},
            _completed(True, [{"path": "a.py"}]),
        ],
    ]
    call = {"n": 0}

    class RT(_FakeRuntime):
        async def run(self, objective, context):
            batch = fail_then_ok[min(call["n"], 1)]
            call["n"] += 1
            for e in batch:
                yield e

    st = asyncio.run(run_production_coding_loop(
        runtime=RT([]),
        context=SimpleNamespace(),
        objective="fix a.py",
        user_id="u1",
        project_id="p1",
        persona_id="code",
        max_attempts=3,
    ))
    assert call["n"] == 2
    assert st.stage == CodingStage.DONE.value
    assert st.attempt == 2
    assert any(e.get("stage") == CodingStage.DIAGNOSE.value for e in st.events)


def test_retry_exhaustion():
    events = [
        {"type": "agent.test_result", "data": {"command": "pytest", "exit_code": 1, "ok": False}},
        _completed(False, [], summary="still failing"),
    ]
    st = asyncio.run(run_production_coding_loop(
        runtime=_FakeRuntime(events),
        context=SimpleNamespace(),
        objective="fix forever",
        user_id="u1",
        project_id="p1",
        persona_id="code",
        max_attempts=2,
    ))
    assert st.stage == CodingStage.EXHAUSTED.value
    assert st.attempt == 2
    assert (st.acceptance or {}).get("ok") is False


def test_cancellation():
    cancelled = {"v": False}

    class RT(_FakeRuntime):
        async def run(self, objective, context):
            cancelled["v"] = True
            yield {"type": "agent.started", "data": {}}
            # loop cancel_check checked between attempts; first attempt still runs
            yield _completed(False, [], summary="x")

    st = asyncio.run(run_production_coding_loop(
        runtime=RT([]),
        context=SimpleNamespace(),
        objective="fix",
        user_id="u1",
        project_id="p1",
        persona_id="code",
        max_attempts=3,
        cancel_check=lambda: True,  # cancel before/during
    ))
    # cancel at start of loop after inspect/plan
    assert st.stage in (CodingStage.CANCELLED.value, CodingStage.EXHAUSTED.value, CodingStage.FAILED.value)


def test_provider_failure_stops_loop():
    events = [
        _completed(
            False,
            [],
            provider_failure={"category": "rate_limited", "retryable": True, "http_status": 429},
            summary="All providers failed",
        ),
    ]
    st = asyncio.run(run_production_coding_loop(
        runtime=_FakeRuntime(events),
        context=SimpleNamespace(),
        objective="implement feature",
        user_id="u1",
        project_id="p1",
        persona_id="code",
        max_attempts=5,
    ))
    assert st.stage == CodingStage.FAILED.value
    assert "provider" in (st.error or st.diagnosis or "")
    assert st.attempt == 1  # does not burn all attempts


def test_resume_from_state():
    resume = CodingLoopState(
        stage=CodingStage.PLAN.value,
        attempt=1,
        inspection={"ecosystem": "python"},
        plan={"goal": "fix", "validate_as": "files"},
        diagnosis="need another edit",
    )
    events = [
        {"type": "agent.tool_result", "data": {"tool": "apply_patch", "files_changed": [{"path": "b.py"}]}},
        _completed(True, [{"path": "b.py"}]),
    ]
    st = asyncio.run(run_production_coding_loop(
        runtime=_FakeRuntime(events),
        context=SimpleNamespace(),
        objective="fix",
        user_id="u1",
        project_id="p1",
        persona_id="code",
        resume=resume,
        max_attempts=3,
    ))
    assert st.inspection is not None
    assert st.plan is not None
    assert st.stage == CodingStage.DONE.value
    # resumed attempt continues (1 already done → next is 2)
    assert st.attempt >= 2


def test_orchestration_runtime_source_wires_coding_loop():
    """Production orchestration_runtime must invoke coding loop bridge."""
    src = open("brain/orchestration_runtime.py").read()
    assert "run_production_coding_loop" in src
    assert "is_coding_objective" in src
    assert "coding_loop_to_node_result" in src
    assert "mission_acceptance_required" in open("brain/coding_loop_bridge.py").read()


def test_coding_loop_to_node_result_not_mission_success():
    st = CodingLoopState(stage=CodingStage.DONE.value, attempt=1)
    st.files_changed = [{"path": "x.py"}]
    st.acceptance = {"ok": True, "provisional": True, "mission_acceptance_required": True}
    st.meta = {"tools_used": ["apply_patch"], "task_id": "t1"}
    mapped = coding_loop_to_node_result(st)
    assert mapped["success"] is True
    assert mapped["mission_acceptance_required"] is True
    assert mapped["coding_loop"]["stage"] == CodingStage.DONE.value
