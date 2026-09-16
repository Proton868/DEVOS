"""Dialect-aware engine pools: SQLite tests vs Postgres production bounds."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_async_engine_kwargs_sqlite_omits_queue_pool_args():
    from core.database import _async_engine_kwargs

    kw = _async_engine_kwargs("sqlite+aiosqlite:///./data/x.db", echo=False)
    assert "pool_size" not in kw
    assert "max_overflow" not in kw
    assert "pool_timeout" not in kw


def test_async_engine_kwargs_postgres_keeps_bounded_pool():
    from core.database import _async_engine_kwargs

    kw = _async_engine_kwargs("postgresql+asyncpg://u:p@localhost/db", echo=False)
    assert kw["pool_size"] == 2
    assert kw["max_overflow"] == 0
    assert kw["pool_timeout"] == 30
    assert kw.get("pool_pre_ping") is True


def test_sync_engine_kwargs_sqlite_uses_nullpool():
    from core.sync_session import _sync_engine_kwargs
    from sqlalchemy.pool import NullPool

    kw = _sync_engine_kwargs("sqlite:///./data/x.db")
    assert kw.get("poolclass") is NullPool
    assert "pool_size" not in kw


def test_sync_engine_kwargs_postgres_keeps_bounded_pool():
    from core.sync_session import _sync_engine_kwargs

    kw = _sync_engine_kwargs("postgresql+psycopg://u:p@localhost/db")
    assert kw["pool_size"] == 2
    assert kw["max_overflow"] == 0
    assert kw["pool_timeout"] == 30


def test_sqlite_async_engine_initializes(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    url = f"sqlite+aiosqlite:///{tmp_path}/async_pool.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from core.database import replace_async_engine, dispose_async_engine

    eng = replace_async_engine(url)
    assert eng is not None
    assert eng.dialect.name.startswith("sqlite")
    dispose_async_engine()


def test_sqlite_sync_engine_initializes(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    url = f"sqlite:///{tmp_path}/sync_pool.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from core.config import settings
    from core.sync_session import dispose_sync_engine, get_sync_engine

    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "REQUIRE_POSTGRES", False)
    dispose_sync_engine()
    eng = get_sync_engine()
    assert eng.dialect.name.startswith("sqlite")
    dispose_sync_engine()
