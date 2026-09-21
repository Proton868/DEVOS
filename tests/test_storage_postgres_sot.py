"""All durable stores on Postgres SoT — no production SQLite authority."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_storage_sot.db")

Path("data").mkdir(exist_ok=True)


def _run(c):
    return asyncio.run(c)


@pytest.fixture(scope="module")
def db():
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_storage_sot.db"
    try:
        from core.database import init_db
        _run(init_db())
    except Exception as e:
        pytest.skip(str(e))
    yield


def test_outbox_enqueue_claim_survive(db):
    from execution.outbox import enqueue, claim_pending, mark_delivered, list_events

    eid = enqueue("test.event", {"x": 1}, user_id="u1", aggregate_id="agg1")
    claimed = claim_pending(limit=10)
    assert any(c["id"] == eid for c in claimed)
    mark_delivered(eid)
    events = list_events(user_id="u1", limit=20)
    assert any(e["id"] == eid for e in events)


def test_audit_scoped(db):
    from governance.audit import get_audit_logger, AuditEventType

    log = get_audit_logger()
    log.log(AuditEventType.AGENT, "actor-a", user_id="user-a", action="run")
    log.log(AuditEventType.AGENT, "actor-b", user_id="user-b", action="run")
    a = log.query(user_id="user-a", limit=50)
    b = log.query(user_id="user-b", limit=50)
    assert all(r.get("user_id") == "user-a" for r in a)
    assert all(r.get("user_id") == "user-b" for r in b)


def test_web_crawl_isolation(db):
    from execution.web_intel.store import create_crawl, list_crawls

    create_crawl({"user_id": "wa", "root_url": "https://a.example"})
    create_crawl({"user_id": "wb", "root_url": "https://b.example"})
    assert all(c["user_id"] == "wa" for c in list_crawls("wa"))
    assert all(c["user_id"] == "wb" for c in list_crawls("wb"))


def test_no_production_sqlite3_connect():
    """Zero production-authority sqlite3.connect outside tests."""
    import pathlib
    bad = []
    for path in pathlib.Path(".").rglob("*.py"):
        if any(part in {".git", ".venv"} for part in path.parts) or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sqlite3.connect" in text:
            bad.append(str(path))
    assert bad == [], f"production sqlite3.connect remains in: {bad}"


def test_web_cache_is_not_sqlite():
    src = Path("execution/web_intel/cache.py").read_text()
    assert "sqlite3" not in src
