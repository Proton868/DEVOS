"""Stage 3M — consequential operation ledger."""
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


def _worker(db_path: Path, body: str) -> dict:
    bootstrap = (
        "import asyncio, json, os, sys, types\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        f"os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///{db_path.as_posix()}'\n"
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
        + textwrap.dedent(body).lstrip()
    )
    r = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"},
    )
    if r.returncode != 0:
        raise AssertionError(f"rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}")
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("{")]
    return json.loads(lines[-1]) if lines else {}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "op.db"
    _worker(
        path,
        """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    print(json.dumps({"ok": True}))
asyncio.run(main())
""",
    )
    return path


def test_reserve_mark_complete_success(db):
    r = _worker(
        db,
        """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import (
        reserve_operation, mark_running, complete_operation, load_operation, OP_SUCCEEDED,
    )
    op = await reserve_operation(owner_id="u", tenant_id="t", tool_name="apply_patch", args={"path": "a.py"})
    assert op
    assert await mark_running(op)
    assert await complete_operation(op, success=True, result_digest="abc")
    row = await load_operation(op)
    print(json.dumps({"status": row["status"], "ok": row["status"] == OP_SUCCEEDED}))
asyncio.run(main())
""",
    )
    assert r["ok"] is True


def test_running_no_evidence_becomes_unknown(db):
    r = _worker(
        db,
        """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import (
        reserve_operation, mark_running, reconcile_operation, OP_UNKNOWN,
    )
    op = await reserve_operation(owner_id="u", tenant_id="t", task_id="task-1", tool_name="rename_file")
    await mark_running(op)
    rec = await reconcile_operation(op, expected_task_id="task-1", expected_owner_id="u", expected_tenant_id="t")
    print(json.dumps(rec, default=str))
asyncio.run(main())
""",
    )
    assert r["status"] == "unknown"
    assert r["retry"] is False
    assert r["execute"] is False


def test_unknown_never_retries(db):
    r = _worker(
        db,
        """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation, mark_running, mark_unknown, reconcile_operation
    op = await reserve_operation(owner_id="u", tenant_id="t", tool_name="run_command")
    await mark_running(op)
    await mark_unknown(op)
    rec = await reconcile_operation(op, expected_owner_id="u")
    print(json.dumps({"status": rec["status"], "retry": rec["retry"], "execute": rec["execute"]}))
asyncio.run(main())
""",
    )
    assert r["status"] == "unknown"
    assert r["retry"] is False
    assert r["execute"] is False


def test_tenant_mismatch_fail_closed(db):
    r = _worker(
        db,
        """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation, mark_running, reconcile_operation
    op = await reserve_operation(owner_id="u", tenant_id="tenant-a", tool_name="delete_file")
    await mark_running(op)
    rec = await reconcile_operation(op, expected_owner_id="u", expected_tenant_id="tenant-b")
    print(json.dumps(rec, default=str))
asyncio.run(main())
""",
    )
    assert r["ok"] is False
    assert r["reason_code"] == "tenant_mismatch"
    assert r["execute"] is False


def test_process_crash_running_to_unknown(db):
    """Real subprocess: reserve+running in child, parent reconciles to UNKNOWN."""
    # Process A
    a = _worker(
        db,
        """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation, mark_running
    op = await reserve_operation(owner_id="u", tenant_id="t", task_id="t1", tool_name="apply_patch", args={"p": "x"})
    await mark_running(op)
    print(json.dumps({"op": op}))
    # crash before complete
asyncio.run(main())
""",
    )
    op = a["op"]
    b = _worker(
        db,
        f"""
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reconcile_operation
    rec = await reconcile_operation({op!r}, expected_owner_id="u", expected_tenant_id="t", expected_task_id="t1")
    print(json.dumps(rec, default=str))
asyncio.run(main())
""",
    )
    assert b["status"] == "unknown"
    assert b["execute"] is False
    assert b["retry"] is False


def test_is_consequential_classification():
    from governance.execution_operations import is_consequential_side_effect
    assert is_consequential_side_effect("local") is True
    assert is_consequential_side_effect("none") is False
    assert is_consequential_side_effect("unknown") is True



def test_reserved_cannot_jump_to_succeeded(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation, transition_operation, load_operation, OP_SUCCEEDED
    op = await reserve_operation(owner_id="u", tenant_id="t", tool_name="x")
    ok = await transition_operation(op, OP_SUCCEEDED)
    row = await load_operation(op)
    print(json.dumps({"ok": ok, "status": row["status"]}))
asyncio.run(main())
""")
    assert r["ok"] is False
    assert r["status"] == "reserved"


def test_mismatched_evidence_yields_unknown(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import EvidenceRecord
    from governance.execution_operations import (
        reserve_operation, mark_running, reconcile_operation,
    )
    from datetime import datetime, timezone
    op = await reserve_operation(owner_id="u", tenant_id="tenant-a", task_id="task-1",
                                 tool_name="apply_patch", correlation_id="c1")
    await mark_running(op)
    async with dbmod.AsyncSessionLocal() as session:
        session.add(EvidenceRecord(
            owner_id="attacker", tenant_id="evil", goal="x",
            operation_id=op,
            body={"operation_id": op, "outcome": "succeeded", "tool": "apply_patch",
                  "tenant_id": "evil", "task_id": "task-1"},
        ))
        await session.commit()
    rec = await reconcile_operation(op, expected_owner_id="u", expected_tenant_id="tenant-a",
                                    expected_task_id="task-1", expected_correlation_id="c1")
    print(json.dumps(rec, default=str))
asyncio.run(main())
""")
    assert r["status"] == "unknown"
    assert r["execute"] is False
    assert r["retry"] is False
