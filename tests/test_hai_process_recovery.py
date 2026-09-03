"""
Stage 3L — Process-level HAI crash/restart acceptance.

Uses real subprocesses + shared SQLite file. Recovery restores cognitive state;
it does not execute tools or grant authority.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _worker_script(db_path: Path, body: str) -> str:
    """Build a self-contained Python script that uses the repo on sys.path."""
    bootstrap = (
        "import asyncio, json, os, sys, types\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        f"os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///{db_path.as_posix()}'\n"
        # Stub pydantic_settings / core.config so workers need only sqlalchemy stack
        "ps = types.ModuleType('pydantic_settings')\n"
        "class BaseSettings:\n"
        "    def __init__(self, **kw):\n"
        "        for k,v in kw.items(): setattr(self,k,v)\n"
        "ps.BaseSettings = BaseSettings\n"
        "sys.modules['pydantic_settings'] = ps\n"
        "cfg = types.ModuleType('core.config')\n"
        "class Settings:\n"
        f"    DATABASE_URL = 'sqlite+aiosqlite:///{db_path.as_posix()}'\n"
        "    DEBUG = False\n"
        "cfg.Settings = Settings\n"
        "cfg.settings = Settings()\n"
        "sys.modules['core.config'] = cfg\n"
    )
    return bootstrap + textwrap.dedent(body).lstrip()


def _run_worker(db_path: Path, body: str, *, expect_ok: bool = True) -> dict:
    script = _worker_script(db_path, body)
    r = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"},
    )
    if expect_ok and r.returncode != 0:
        raise AssertionError(f"worker failed rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}")
    # Last JSON line is the result
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("{")]
    if not lines:
        return {"_raw_stdout": r.stdout, "_stderr": r.stderr, "_rc": r.returncode}
    return json.loads(lines[-1])


@pytest.fixture
def shared_db(tmp_path):
    db = tmp_path / "stage3l.db"
    # init schema in a subprocess
    _run_worker(
        db,
        """
async def main():
    from core import config as cfg
    cfg.settings.DATABASE_URL = os.environ["DATABASE_URL"]
    # rebuild engine for this URL
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    print(json.dumps({"ok": True, "init": True}))

asyncio.run(main())
""",
    )
    return db


def test_case_a_crash_after_checkpoint(shared_db):
    task_id = str(uuid.uuid4())
    corr = str(uuid.uuid4())
    # Process A: create task + checkpoint, then exit
    a = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        row = AgentTaskRecord(
            id={task_id!r},
            user_id="user-a",
            tenant_id="tenant-a",
            project_id="proj-a",
            session_id="sess-a",
            objective="Fix authentication bug",
            mode="agent",
            status="running",
            correlation_id={corr!r},
            events=[],
            started_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()
    ctrl = StrategicController()
    ctrl.start("Fix authentication bug")
    ctrl.on_tool_result("apply_patch", True, result={{"ok": True}})
    cp = ctrl.control.checkpoint({task_id!r}, correlation_id={corr!r}, state_version=2)
    ok = await persist_hai_checkpoint({task_id!r}, cp.to_dict())
    print(json.dumps({{"ok": ok, "task_id": {task_id!r}, "checksum": cp.checksum, "lifecycle": ctrl.control.lifecycle, "verification": ctrl.control.verification_status}}))
    # deliberate process end
asyncio.run(main())
""",
    )
    assert a.get("ok") is True
    assert a.get("verification") == "required"

    # Process B: recover
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b", job_status=None)
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b.get("ok") is True
    assert b.get("execute") is False
    assert b.get("retry") is False
    assert b["identity"]["user_id"] == "user-a"
    assert b["identity"]["tenant_id"] == "tenant-a"
    assert b["identity"]["correlation_id"] == corr
    assert b["checkpoint"]["task_id"] == task_id
    state = b["checkpoint"]["state"]
    assert "strategic" in state
    assert state["strategic"]["objective"] == "Fix authentication bug"
    assert state.get("verification", {}).get("status") == "required" or state["tactical"].get("verification_status") == "required"


def test_case_b_running_job_wait_no_duplicate(shared_db):
    task_id = str(uuid.uuid4())
    job_id = "job-running-1"
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="running", correlation_id="c1", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    ctrl.control.last_job_id = {job_id!r}
    ctrl.control.last_job_status = "running"
    cp = ctrl.control.checkpoint({task_id!r}, correlation_id="c1", state_version=1)
    await persist_hai_checkpoint({task_id!r}, cp.to_dict())
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b", job_status="running", job_id={job_id!r})
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b["ok"] is True
    assert b["execute"] is False
    assert b["retry"] is False
    assert b["outcome"] == "wait"
    assert b["reconciliation"]["retry"] is False


def test_case_c_unknown_no_retry(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="running", correlation_id="c", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    ctrl.control.last_job_id = "job-unk"
    ctrl.control.last_job_status = "unknown"
    cp = ctrl.control.checkpoint({task_id!r}, state_version=1)
    await persist_hai_checkpoint({task_id!r}, cp.to_dict())
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b", job_status="unknown", job_id="job-unk")
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b["ok"] is True
    assert b["retry"] is False
    assert b["execute"] is False
    assert b["outcome"] == "unknown"


def test_case_d_succeeded_no_rerun(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="running", correlation_id="c", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    ctrl.control.last_job_id = "job-ok"
    ctrl.control.last_job_status = "succeeded"
    cp = ctrl.control.checkpoint({task_id!r}, state_version=1)
    await persist_hai_checkpoint({task_id!r}, cp.to_dict())
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b", job_status="succeeded", job_id="job-ok")
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b["ok"] is True
    assert b["retry"] is False
    assert b["execute"] is False
    assert b["outcome"] == "continue"


def test_case_e_failed_replan_no_retry_flag(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="running", correlation_id="c", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    ctrl.control.last_job_status = "failed"
    cp = ctrl.control.checkpoint({task_id!r}, state_version=1)
    await persist_hai_checkpoint({task_id!r}, cp.to_dict())
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b", job_status="failed")
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b["ok"] is True
    assert b["retry"] is False
    assert b["outcome"] == "replan"


def test_case_f_cancelled_terminal(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="cancelled", correlation_id="c", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    cp = ctrl.control.checkpoint({task_id!r}, state_version=1)
    await persist_hai_checkpoint({task_id!r}, cp.to_dict())
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b")
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b["ok"] is True
    assert b["execute"] is False
    assert b["outcome"] == "cancelled"


def test_case_g_corrupt_checkpoint_fail_closed(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="running", correlation_id="c", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    cp = ctrl.control.checkpoint({task_id!r}, state_version=1)
    d = cp.to_dict()
    d["state"]["strategic"]["objective"] = "TAMPERED"
    # keep old checksum → invalid
    await persist_hai_checkpoint({task_id!r}, d)
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b")
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    assert b.get("ok") is False
    assert b.get("reason_code") == "checkpoint_invalid"
    assert b.get("execute") is False


def test_recovery_lease_race(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint, claim_task_recovery
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="u", tenant_id="t", project_id="p", session_id="s",
            objective="x", mode="agent", status="running", correlation_id="c", events=[],
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    await persist_hai_checkpoint({task_id!r}, ctrl.control.checkpoint({task_id!r}).to_dict())
    ok = await claim_task_recovery({task_id!r}, "worker-b", lease_seconds=120)
    print(json.dumps({{"claimed_b": ok}}))
asyncio.run(main())
""",
    )
    c = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.agent_task_store import claim_task_recovery
    ok = await claim_task_recovery({task_id!r}, "worker-c", lease_seconds=120)
    print(json.dumps({{"claimed_c": ok}}))
asyncio.run(main())
""",
    )
    assert c.get("claimed_c") is False


def test_identity_not_from_checkpoint(shared_db):
    task_id = str(uuid.uuid4())
    _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import AgentTaskRecord
    from brain.agent_task_store import persist_hai_checkpoint
    from cognitive.hai_control import StrategicController
    from datetime import datetime, timezone
    async with dbmod.AsyncSessionLocal() as session:
        session.add(AgentTaskRecord(
            id={task_id!r}, user_id="real-user", tenant_id="real-tenant", project_id="p",
            session_id="s", objective="x", mode="agent", status="running", correlation_id="c",
            events=[], started_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    ctrl = StrategicController(); ctrl.start("x")
    # Try to poison checkpoint with identity — build_checkpoint strips, and recovery uses task row
    cp = ctrl.control.checkpoint({task_id!r}).to_dict()
    cp["user_id"] = "attacker"  # forbidden at load
    # If checksum still matches only because we didn't change signed body fields incorrectly —
    # changing top-level user_id will fail checksum OR be rejected by from_dict
    await persist_hai_checkpoint({task_id!r}, cp)
    print(json.dumps({{"ok": True}}))
asyncio.run(main())
""",
    )
    b = _run_worker(
        shared_db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from brain.hai_recovery import recover_hai_task, finish_recovery
    res = await recover_hai_task({task_id!r}, owner_id="worker-b")
    await finish_recovery({task_id!r}, "worker-b")
    print(json.dumps(res, default=str))
asyncio.run(main())
""",
    )
    # Either invalid checkpoint or recovered with real identity only
    if b.get("ok"):
        assert b["identity"]["user_id"] == "real-user"
        assert b["identity"]["tenant_id"] == "real-tenant"
        assert "attacker" not in json.dumps(b)
    else:
        assert b.get("reason_code") in ("checkpoint_invalid", "checkpoint_missing")
        assert b.get("execute") is False
