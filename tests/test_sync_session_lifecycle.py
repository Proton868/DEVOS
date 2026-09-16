"""Regression: sync SQLAlchemy pool must not grow without bound (Supabase EMAXCONNSESSION)."""
from __future__ import annotations

from core import sync_session as ss


def test_sync_engine_singleton_same_url(monkeypatch):
    monkeypatch.setattr("core.config.settings.REQUIRE_POSTGRES", False)
    monkeypatch.setattr(
        "core.config.settings.DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_sync_lifecycle.db",
    )
    ss.dispose_sync_engine()
    e1 = ss.get_sync_engine()
    e2 = ss.get_sync_engine()
    e3 = ss.get_sync_engine()
    assert e1 is e2 is e3
    ss.dispose_sync_engine()


def test_sync_engine_replace_disposes_previous(monkeypatch):
    monkeypatch.setattr("core.config.settings.REQUIRE_POSTGRES", False)
    monkeypatch.setattr(
        "core.config.settings.DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_sync_lifecycle_a.db",
    )
    ss.dispose_sync_engine()
    e1 = ss.get_sync_engine()
    disposed = {"n": 0}
    old_dispose = e1.dispose

    def _disp(*a, **k):
        disposed["n"] += 1
        return old_dispose(*a, **k)

    e1.dispose = _disp  # type: ignore[method-assign]
    monkeypatch.setattr(
        "core.config.settings.DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_sync_lifecycle_b.db",
    )
    e2 = ss.get_sync_engine()
    assert e2 is not e1
    assert disposed["n"] >= 1
    ss.dispose_sync_engine()


def test_repeated_saga_ops_reuse_pool(monkeypatch):
    monkeypatch.setattr("core.config.settings.REQUIRE_POSTGRES", False)
    monkeypatch.setattr(
        "core.config.settings.DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_saga_pool.db",
    )
    ss.dispose_sync_engine()
    from core.database import Base
    eng = ss.get_sync_engine()
    Base.metadata.create_all(eng)

    from execution.saga import create_saga, begin_step, complete_step, load_saga

    engines = []
    for i in range(40):
        s = create_saga(plan_id=f"pool-{i}")
        st = begin_step(s, node_id="n1", action="preview")
        complete_step(st)
        loaded = load_saga(s.saga_id)
        assert loaded is not None
        engines.append(ss.get_sync_engine())

    assert all(e is engines[0] for e in engines)
    ss.dispose_sync_engine()


def test_session_context_closes(monkeypatch):
    monkeypatch.setattr("core.config.settings.REQUIRE_POSTGRES", False)
    monkeypatch.setattr(
        "core.config.settings.DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_sync_close.db",
    )
    ss.dispose_sync_engine()
    from sqlalchemy import text

    with ss.get_sync_session() as s:
        s.execute(text("SELECT 1"))
    with ss.get_sync_session() as s2:
        s2.execute(text("SELECT 1"))
    ss.dispose_sync_engine()
