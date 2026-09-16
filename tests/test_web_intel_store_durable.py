"""Durable web_intel store — pages/events persist (SQLite test isolation)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["REQUIRE_POSTGRES"] = "false"
Path("data").mkdir(exist_ok=True)


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/web_intel.db"
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("DATABASE_URL", url)
    from core.config import settings
    from core.sync_session import dispose_sync_engine
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "REQUIRE_POSTGRES", False)
    dispose_sync_engine()
    yield
    dispose_sync_engine()


def test_create_crawl_returns_queued(iso):
    from execution.web_intel.store import create_crawl, get_crawl
    c = create_crawl({
        "user_id": "u1",
        "root_url": "http://example.com/",
        "normalized_root_url": "http://example.com/",
        "max_pages": 3,
    })
    assert c["status"] == "QUEUED"
    assert c["crawl_id"]
    assert get_crawl(c["crawl_id"])["user_id"] == "u1"


def test_page_upsert_claim_and_events(iso):
    from execution.web_intel.store import (
        create_crawl, upsert_page, list_pages, claim_queued_pages,
        emit_event, list_events, update_crawl, get_crawl,
    )
    c = create_crawl({"user_id": "u2", "root_url": "http://ex/", "normalized_root_url": "http://ex/"})
    cid = c["crawl_id"]
    pid = upsert_page({
        "crawl_id": cid, "url": "http://ex/", "normalized_url": "http://ex/",
        "depth": 0, "status": "QUEUED",
    })
    assert pid
    claimed = claim_queued_pages(cid, limit=5)
    assert len(claimed) == 1
    assert claimed[0]["status"] == "FETCHING"
    pages = list_pages(cid, status="FETCHING")
    assert len(pages) == 1
    upsert_page({**pages[0], "status": "EXTRACTED", "title": "Hi"})
    assert list_pages(cid, status="FETCHING") == []
    emit_event(cid, "crawl.started", {"root": "http://ex/"})
    emit_event(cid, {"type": "crawl.completed", "pages": 1})
    ev = list_events(cid)
    assert len(ev) >= 2
    types = {e.get("type") or e.get("event_type") for e in ev}
    assert "crawl.started" in types
    update_crawl(cid, status="COMPLETED")
    assert get_crawl(cid)["status"] == "COMPLETED"


def test_user_isolation_list_crawls(iso):
    from execution.web_intel.store import create_crawl, list_crawls
    create_crawl({"user_id": "a", "root_url": "http://a/"})
    create_crawl({"user_id": "b", "root_url": "http://b/"})
    assert all(c["user_id"] == "a" for c in list_crawls("a"))
    assert all(c["user_id"] == "b" for c in list_crawls("b"))
