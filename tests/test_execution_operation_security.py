"""Stage 3M.1 — identity/evidence security for operation ledger."""
from __future__ import annotations
import json, os, subprocess, sys, textwrap
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
    path = tmp_path / "sec.db"
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

def test_null_tenant_not_wildcard(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation, mark_running, reconcile_operation
    op = await reserve_operation(owner_id="u", tenant_id=None, task_id="t1", tool_name="x")
    await mark_running(op)
    rec = await reconcile_operation(op, expected_owner_id="u", expected_tenant_id="tenant-A", expected_task_id="t1")
    print(json.dumps(rec, default=str))
asyncio.run(main())
""")
    assert r["ok"] is False
    assert r["reason_code"] == "tenant_mismatch"
    assert r["execute"] is False

def test_terminal_immutable(db):
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
    assert await complete_operation(op, success=True)
    # cannot overwrite terminal
    ok = await mark_unknown(op)
    row = await load_operation(op)
    print(json.dumps({"mark_unknown": ok, "status": row["status"]}))
asyncio.run(main())
""")
    assert r["mark_unknown"] is False
    assert r["status"] == "succeeded"

def test_invalid_transition_reserved_to_succeeded(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation, complete_operation, load_operation
    op = await reserve_operation(owner_id="u", tenant_id="t", tool_name="x")
    ok = await complete_operation(op, success=True)
    row = await load_operation(op)
    print(json.dumps({"ok": ok, "status": row["status"]}))
asyncio.run(main())
""")
    assert r["ok"] is False
    assert r["status"] == "reserved"

def test_idempotency_returns_same_id(db):
    r = _worker(db, """
async def main():
    from core import database as dbmod
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    dbmod.engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    from governance.execution_operations import reserve_operation
    a = await reserve_operation(owner_id="u", tenant_id="t", operation_type="tool",
                                idempotency_key="idem-1", tool_name="apply_patch")
    b = await reserve_operation(owner_id="u", tenant_id="t", operation_type="tool",
                                idempotency_key="idem-1", tool_name="apply_patch")
    print(json.dumps({"same": a == b, "a": a, "b": b}))
asyncio.run(main())
""")
    assert r["same"] is True

def test_secret_scrubbed_from_digest_path(db):
    r = _worker(db, """
async def main():
    from governance.execution_operations import _scrub_args
    scrubbed = _scrub_args({"path": "a.py", "api_key": "SECRET", "password": "x"})
    print(json.dumps(scrubbed))
asyncio.run(main())
""")
    assert r["api_key"] == "[redacted]"
    assert r["password"] == "[redacted]"
    assert r["path"] == "a.py"
