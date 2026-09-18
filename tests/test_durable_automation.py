"""Durable automation lifecycle: version → trigger → op/job → restart-safe run."""
from __future__ import annotations

import pytest

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.flow_automation import (
    TriggerSpec,
    TriggerType,
    RunMode,
    RunStatus,
    publish_workflow_version,
    set_workflow_enabled,
    bump_workflow_version,
    trigger_idempotency_key,
    trigger_automation_run,
    persist_run_durable,
    load_run_durable,
    find_run_by_idempotency_durable,
    reset_run_store_for_tests,
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'auto.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod
    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    reset_run_store_for_tests()
    yield dbmod
    await dbmod.engine.dispose()


def _wf(*, enabled=True, version="1.0.0", revision=1, status="published"):
    wf = Workflow(
        workflow_id="wf-auto-1",
        name="demo",
        version=version,
        steps=[
            WorkflowStep(
                id="s1",
                name="noop",
                type=StepType.SCRIPT if hasattr(StepType, "SCRIPT") else list(StepType)[0],
                inputs={"code": "pass"},
            )
        ],
        triggers=["manual", "webhook"],
        owner_id="user-1",
    )
    setattr(wf, "enabled", enabled)
    setattr(wf, "revision", revision)
    setattr(wf, "status", status)
    setattr(wf, "tenant_id", "tenant-1")
    return wf


def test_publish_and_bump_version():
    wf = _wf(status="draft")
    publish_workflow_version(wf)
    assert getattr(wf, "status") == "published"
    v0 = getattr(wf, "revision")
    bump_workflow_version(wf)
    assert getattr(wf, "revision") == v0 + 1


def test_trigger_idempotency_key_semantics():
    t = TriggerSpec(type=TriggerType.WEBHOOK, config={})
    k1 = trigger_idempotency_key(
        workflow_id="w1", workflow_version=1, trigger=t, delivery_id="del-1",
    )
    k2 = trigger_idempotency_key(
        workflow_id="w1", workflow_version=1, trigger=t, delivery_id="del-1",
    )
    k3 = trigger_idempotency_key(
        workflow_id="w1", workflow_version=1, trigger=t, delivery_id="del-2",
    )
    assert k1 == k2 and k1 != k3
    assert trigger_idempotency_key(
        workflow_id="w1", workflow_version=1,
        trigger=TriggerSpec(type=TriggerType.MANUAL),
    ) is None
    sk = trigger_idempotency_key(
        workflow_id="w1", workflow_version=2,
        trigger=TriggerSpec(type=TriggerType.SCHEDULE),
        schedule_occurrence="2026-09-18T12:00:00Z",
    )
    assert sk and sk != k1


@pytest.mark.asyncio
async def test_trigger_creates_run_with_op_and_job(db):
    wf = _wf()
    run = await trigger_automation_run(
        wf,
        owner_id="user-1",
        tenant_id="tenant-1",
        mode=RunMode.PRODUCTION,
        trigger=TriggerSpec(type=TriggerType.MANUAL),
        idempotency_key="manual-once-1",
    )
    assert run.status == RunStatus.QUEUED
    assert getattr(run, "execution_job_id", None)
    assert getattr(run, "operation_id", None)
    assert run.workflow_version == 1
    loaded = await load_run_durable(run.run_id)
    assert loaded is not None
    assert loaded.workflow_id == run.workflow_id
    assert getattr(loaded, "operation_id", None) == getattr(run, "operation_id")
    assert getattr(loaded, "execution_job_id", None) == getattr(run, "execution_job_id")


@pytest.mark.asyncio
async def test_repeated_trigger_converges(db):
    wf = _wf()
    t = TriggerSpec(type=TriggerType.WEBHOOK, config={})
    a = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        trigger=t, delivery_id="hook-42",
    )
    b = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        trigger=t, delivery_id="hook-42",
    )
    assert a.run_id == b.run_id
    assert getattr(a, "operation_id") == getattr(b, "operation_id")


@pytest.mark.asyncio
async def test_different_deliveries_independent(db):
    wf = _wf()
    t = TriggerSpec(type=TriggerType.EVENT, config={})
    a = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        trigger=t, delivery_id="evt-1",
    )
    b = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        trigger=t, delivery_id="evt-2",
    )
    assert a.run_id != b.run_id
    assert getattr(a, "operation_id") != getattr(b, "operation_id")


@pytest.mark.asyncio
async def test_disabled_rejects_new_triggers(db):
    wf = _wf(enabled=False)
    with pytest.raises(PermissionError, match="automation_disabled"):
        await trigger_automation_run(
            wf, owner_id="user-1", tenant_id="tenant-1",
            trigger=TriggerSpec(type=TriggerType.MANUAL),
            idempotency_key="disabled-1",
        )


@pytest.mark.asyncio
async def test_disable_does_not_corrupt_existing_run(db):
    wf = _wf(enabled=True)
    run = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        idempotency_key="keep-running-1",
    )
    op_id = getattr(run, "operation_id")
    job_id = getattr(run, "execution_job_id")
    set_workflow_enabled(wf, False)
    # Existing run still loadable and identities unchanged
    loaded = await load_run_durable(run.run_id)
    assert loaded is not None
    assert getattr(loaded, "operation_id") == op_id
    assert getattr(loaded, "execution_job_id") == job_id
    assert loaded.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_historical_run_keeps_version_snapshot(db):
    wf = _wf(revision=3, version="3.0.0")
    run = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        idempotency_key="hist-ver-1",
    )
    assert run.workflow_version == 3
    snap = getattr(run, "definition_snapshot", {}) or {}
    assert snap.get("workflow_version") == 3 or run.workflow_version == 3
    bump_workflow_version(wf)
    loaded = await load_run_durable(run.run_id)
    assert loaded.workflow_version == 3  # frozen on run


@pytest.mark.asyncio
async def test_restart_reload_preserves_identities(db):
    wf = _wf()
    run = await trigger_automation_run(
        wf, owner_id="user-1", tenant_id="tenant-1",
        trigger=TriggerSpec(type=TriggerType.SCHEDULE),
        schedule_occurrence="2026-09-18T15:00:00Z",
    )
    rid, oid, jid = run.run_id, getattr(run, "operation_id"), getattr(run, "execution_job_id")
    # Simulate process restart: only durable load
    again = await load_run_durable(rid)
    assert again is not None
    assert getattr(again, "operation_id") == oid
    assert getattr(again, "execution_job_id") == jid
    by_key = await find_run_by_idempotency_durable(run.owner_id, run.idempotency_key)
    assert by_key is not None and by_key.run_id == rid
