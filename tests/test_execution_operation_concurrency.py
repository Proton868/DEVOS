"""Stage 3M.2 — concurrent operation integrity."""
from __future__ import annotations
import asyncio, json, os, subprocess, sys, textwrap, threading
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]

def _worker(db_path, body):
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
    r = subprocess.run([sys.executable, "-c", bootstrap], cwd=str(REPO), capture_output=True, text=True, timeout=60,
                       env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}"})
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("{")]
    return json.loads(lines[-1]) if lines else {}

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.db"
    _worker(path, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    print(json.dumps({"ok": True}))
asyncio.run(main())
""")
    return path

def test_duplicate_idempotency_same_id(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation
    a = await reserve_operation(owner_id="u", tenant_id="t", operation_type="tool",
                                idempotency_key="k1", tool_name="apply_patch")
    b = await reserve_operation(owner_id="u", tenant_id="t", operation_type="tool",
                                idempotency_key="k1", tool_name="apply_patch")
    print(json.dumps({"same": a == b and a is not None}))
asyncio.run(main())
""")
    assert r["same"] is True

def test_different_tenant_separate_ops(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation
    a = await reserve_operation(owner_id="u", tenant_id="t1", operation_type="tool",
                                idempotency_key="k1", tool_name="x")
    b = await reserve_operation(owner_id="u", tenant_id="t2", operation_type="tool",
                                idempotency_key="k1", tool_name="x")
    print(json.dumps({"different": bool(a and b and a != b)}))
asyncio.run(main())
""")
    assert r["different"] is True

def test_double_complete_one_wins(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import (
        reserve_operation, mark_running, complete_operation, mark_unknown, load_operation,
    )
    op = await reserve_operation(owner_id="u", tenant_id="t", tool_name="x")
    await mark_running(op)
    ok1 = await complete_operation(op, success=True)
    ok2 = await mark_unknown(op)
    row = await load_operation(op)
    print(json.dumps({"ok1": ok1, "ok2": ok2, "status": row["status"]}))
asyncio.run(main())
""")
    assert r["ok1"] is True
    assert r["ok2"] is False
    assert r["status"] == "succeeded"


def test_historical_null_operation_id_not_replayable(db):
    """Legacy consequential job with NULL operation_id must fail closed on stale recovery."""
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from core.database import ExecutionJob
    from workers.job_queue import recover_stale_leases
    from datetime import datetime, timezone, timedelta
    async with dbmod.AsyncSessionLocal() as session:
        session.add(ExecutionJob(
            id="legacy-job-1",
            tenant_id="t",
            owner_id="u",
            job_type="script",  # consequential
            payload={},
            status="running",
            operation_id=None,
            locked_at=datetime.now(timezone.utc) - timedelta(hours=2),
            lease_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ))
        await session.commit()
    n = await recover_stale_leases()
    async with dbmod.AsyncSessionLocal() as session:
        job = await session.get(ExecutionJob, "legacy-job-1")
        print(json.dumps({
            "status": job.status,
            "error": job.error or "",
            "not_queued": job.status != "queued",
        }))
asyncio.run(main())
""")
    assert r["status"] == "failed"
    assert r["not_queued"] is True
    assert "missing_operation" in (r.get("error") or "")
