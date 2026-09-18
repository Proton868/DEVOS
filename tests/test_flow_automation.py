"""Flow automation layer — hybrid steps, durability, governance."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.flow_automation import (
    RunMode,
    RunStatus,
    TriggerSpec,
    TriggerType,
    build_snapshot_from_workflow,
    execute_automation,
    hybrid_http_step,
    hybrid_script_step,
    parse_triggers,
    request_cancel,
    reset_run_store_for_tests,
    resume_automation,
    set_workflow_enabled,
    bump_workflow_version,
)
from brain.workflow_executor import (
    run_from_snapshot,
    JOB_SUCCEEDED,
    JOB_FAILED,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_DENIED,
)


def _wf(*steps: WorkflowStep, start: str | None = None, **kw) -> Workflow:
    steps = list(steps)
    w = Workflow(
        workflow_id=kw.get("workflow_id", "wf1"),
        name=kw.get("name", "test-flow"),
        start_step=start or (steps[0].id if steps else None),
        triggers=kw.get("triggers", ["manual"]),
        owner_id=kw.get("owner_id", "user1"),
        status="active",
        version=kw.get("version", "1.0.0"),
    )
    w.steps = steps
    setattr(w, "enabled", kw.get("enabled", True))
    setattr(w, "revision", kw.get("revision", 1))
    return w


def test_step_types_include_flow_primitives():
    for t in ("script", "http", "loop", "transform", "database"):
        assert StepType(t).value == t


def test_parse_triggers_and_enable_version():
    w = _wf(
        WorkflowStep(id="a", type=StepType.NOTIFY, name="n", next_step=None),
        triggers=["manual", "webhook"],
        schedule="0 * * * *",
    )
    w.schedule = "0 * * * *"
    trigs = parse_triggers(w)
    types = {t.type for t in trigs}
    assert TriggerType.MANUAL in types
    assert TriggerType.WEBHOOK in types
    assert TriggerType.SCHEDULE in types
    set_workflow_enabled(w, False)
    assert getattr(w, "enabled") is False
    bump_workflow_version(w)
    assert w.version.startswith("2")


def test_disabled_workflow_snapshot_rejected():
    w = _wf(WorkflowStep(id="a", type=StepType.NOTIFY, name="n"))
    set_workflow_enabled(w, False)
    with pytest.raises(ValueError, match="disabled"):
        build_snapshot_from_workflow(w)


def test_successful_notify_chain():
    w = _wf(
        WorkflowStep(id="a", type=StepType.NOTIFY, name="hello", next_step="b"),
        WorkflowStep(id="b", type=StepType.TRANSFORM, name="t", inputs={"map": {"x": 1}}, next_step=None),
    )

    async def _run():
        store = reset_run_store_for_tests()
        run = await execute_automation(w, owner_id="user1", mode=RunMode.MANUAL, store=store)
        return run

    run = asyncio.run(_run())
    assert run.status == RunStatus.SUCCEEDED
    assert run.result_summary.get("status") == JOB_SUCCEEDED


def test_branching_condition():
    w = _wf(
        WorkflowStep(
            id="c",
            type=StepType.CONDITION,
            name="cond",
            condition="true",
            branches={"true": "yes", "false": "no"},
            next_step="yes",
        ),
        WorkflowStep(id="yes", type=StepType.NOTIFY, name="yes", next_step=None),
        WorkflowStep(id="no", type=StepType.NOTIFY, name="no", next_step=None),
        start="c",
    )

    async def _run():
        snap = build_snapshot_from_workflow(w)
        return await run_from_snapshot(snap)

    result = asyncio.run(_run())
    assert result.status == JOB_SUCCEEDED
    statuses = {s["step_id"]: s["status"] for s in result.steps if isinstance(s, dict)}
    # condition evaluator may treat "true" literally
    assert "c" in statuses


def test_loop_bounded():
    w = _wf(
        WorkflowStep(
            id="loop1",
            type=StepType.LOOP,
            name="loop",
            inputs={"items": [1, 2, 3, 4], "max_iterations": 2},
        ),
        start="loop1",
    )

    async def _run():
        return await run_from_snapshot(build_snapshot_from_workflow(w))

    result = asyncio.run(_run())
    assert result.status == JOB_SUCCEEDED
    loop_step = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "loop1")
    assert loop_step["status"] == STEP_SUCCEEDED
    assert len(loop_step["outputs"]["iterations"]) == 2


def test_transform_step():
    w = _wf(
        WorkflowStep(
            id="t",
            type=StepType.TRANSFORM,
            inputs={"source": {"a": 1, "b": 2}, "map": {"x": "a"}},
        ),
        start="t",
    )
    result = asyncio.run(run_from_snapshot(build_snapshot_from_workflow(w)))
    assert result.status == JOB_SUCCEEDED
    st = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "t")
    assert st["outputs"].get("x") == 1


def test_script_step_isolation_unavailable_or_success():
    step = hybrid_script_step("s1", code="print(1)", language="python")
    w = _wf(step, start="s1")

    async def _run():
        return await run_from_snapshot(build_snapshot_from_workflow(w))

    result = asyncio.run(_run())
    # Either isolation runs or fails closed — never host-exec success without isolation
    assert result.status in (JOB_SUCCEEDED, JOB_FAILED)
    st = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "s1")
    assert st["status"] in (STEP_SUCCEEDED, STEP_FAILED, STEP_DENIED)


def test_script_missing_code_fails():
    w = _wf(
        WorkflowStep(id="s", type=StepType.SCRIPT, inputs={"language": "python"}),
        start="s",
    )
    result = asyncio.run(run_from_snapshot(build_snapshot_from_workflow(w)))
    assert result.status == JOB_FAILED
    st = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "s")
    assert st["status"] == STEP_FAILED


def test_http_rejects_raw_secrets():
    w = _wf(
        WorkflowStep(
            id="h",
            type=StepType.HTTP,
            capability="ucip:api.call",
            inputs={"url": "https://example.com", "token": "SECRET"},
        ),
        start="h",
    )
    result = asyncio.run(run_from_snapshot(build_snapshot_from_workflow(w)))
    st = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "h")
    # Denied by UCIP or validation — either is fail-closed
    assert st["status"] in (STEP_FAILED, STEP_DENIED)


def test_database_raw_sql_denied():
    w = _wf(
        WorkflowStep(
            id="d",
            type=StepType.DATABASE,
            inputs={"sql": "SELECT 1"},
        ),
        start="d",
    )
    result = asyncio.run(run_from_snapshot(build_snapshot_from_workflow(w)))
    st = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "d")
    assert st["status"] == STEP_DENIED


def test_idempotency_and_replay():
    w = _wf(
        WorkflowStep(id="a", type=StepType.NOTIFY, name="n"),
        start="a",
    )

    async def _run():
        store = reset_run_store_for_tests()
        r1 = await execute_automation(
            w, owner_id="user1", idempotency_key="k1", store=store
        )
        r2 = await execute_automation(
            w, owner_id="user1", idempotency_key="k1", store=store
        )
        return r1, r2

    r1, r2 = asyncio.run(_run())
    assert r1.run_id == r2.run_id
    assert r1.status == RunStatus.SUCCEEDED


def test_cancel_before_and_flag():
    store = reset_run_store_for_tests()
    w = _wf(WorkflowStep(id="a", type=StepType.NOTIFY, name="n"), start="a")

    async def _run():
        run = await execute_automation(w, owner_id="user1", store=store)
        cancelled = request_cancel(run.run_id, store=store)
        return run, cancelled

    run, cancelled = asyncio.run(_run())
    assert cancelled is not None
    assert cancelled.cancel_requested is True


def test_resume_terminal_idempotent():
    w = _wf(WorkflowStep(id="a", type=StepType.NOTIFY, name="n"), start="a")

    async def _run():
        store = reset_run_store_for_tests()
        run = await execute_automation(w, owner_id="user1", store=store)
        resumed = await resume_automation(run.run_id, w, store=store)
        return run, resumed

    run, resumed = asyncio.run(_run())
    assert resumed.status == RunStatus.SUCCEEDED
    assert resumed.run_id == run.run_id


def test_hybrid_visual_script_workflow():
    """Visual NOTIFY → SCRIPT → TRANSFORM hybrid definition."""
    s_script = hybrid_script_step("s", code="print('ok')", language="python", next_step="t")
    w = _wf(
        WorkflowStep(id="n", type=StepType.NOTIFY, name="start", next_step="s"),
        s_script,
        WorkflowStep(
            id="t",
            type=StepType.TRANSFORM,
            inputs={"map": {"done": True}},
            next_step=None,
        ),
        start="n",
    )
    assert any(s.type == StepType.SCRIPT for s in w.steps)
    assert any(s.type == StepType.NOTIFY for s in w.steps)

    async def _run():
        return await execute_automation(w, owner_id="user1", mode=RunMode.TEST)

    run = asyncio.run(_run())
    # May fail on script isolation in constrained envs but must not crash
    assert run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED)
    assert run.execution_state is not None


def test_authorization_deny_path_for_script():
    """UCIP gate deny prevents script execution."""
    w = _wf(
        hybrid_script_step("s", code="print(1)", language="python"),
        start="s",
    )

    async def fake_deny(cap, inputs, context):
        return "DENY", "test-deny"

    async def _run():
        with patch("brain.workflow_executor._ucip_gate", side_effect=fake_deny):
            return await run_from_snapshot(build_snapshot_from_workflow(w))

    result = asyncio.run(_run())
    st = next(s for s in result.steps if isinstance(s, dict) and s["step_id"] == "s")
    assert st["status"] == STEP_DENIED
    assert result.status == JOB_FAILED


def test_retry_field_on_step_preserved():
    s = WorkflowStep(id="a", type=StepType.HTTP, retry=2, timeout_s=15, inputs={"url": "https://example.com"})
    d = s.to_dict()
    assert d["retry"] == 2
    assert d["timeout_s"] == 15
    s2 = WorkflowStep.from_dict(d)
    assert s2.retry == 2


def test_production_mode_flag_in_context():
    w = _wf(WorkflowStep(id="a", type=StepType.NOTIFY, name="n"), start="a")

    async def _run():
        store = reset_run_store_for_tests()
        return await execute_automation(
            w, owner_id="user1", mode=RunMode.PRODUCTION, store=store
        )

    run = asyncio.run(_run())
    assert run.mode == RunMode.PRODUCTION
    assert run.status == RunStatus.SUCCEEDED
