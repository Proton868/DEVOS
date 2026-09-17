"""Pytest process isolation from production .env Postgres policy.

Production application code keeps REQUIRE_POSTGRES=true fail-closed.
This file only affects the pytest process so local/CI unit tests can use
an isolated SQLite engine without inheriting production DATABASE_URL from .env.

Opt-in real Postgres:
  DEVOS_TEST_DATABASE_URL=postgresql+asyncpg://...

Tests that assert SQLite rejection should monkeypatch REQUIRE_POSTGRES=true
and validate via Settings / ops.env_validate without relying on the process engine.

Modules that need live Postgres should set DATABASE_URL explicitly (not setdefault)
and mark tests with @pytest.mark.postgres (or skip when unreachable).
"""
from __future__ import annotations

import os
from pathlib import Path

_TEST_DB_DEFAULT = "sqlite+aiosqlite:///./data/pytest_isolated.db"


def _is_postgres_url(url: str) -> bool:
    low = (url or "").lower()
    return low.startswith("postgres")


def _apply_test_db_isolation() -> None:
    """Default test-process DB policy (SQLite) unless Postgres was opted in."""
    Path("data").mkdir(exist_ok=True)
    # Real-runtime suite must never inherit FAKE defaults.
    if os.environ.get("DEVOS_REAL_RUNTIME_TESTS") == "1":
        os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
        os.environ.pop("DEVOS_ALLOW_FAKE_RUNTIME", None)
    else:
        os.environ.setdefault("DEVOS_ORCH_FAKE_RUNTIME", "1")
        os.environ.setdefault("DEVOS_ALLOW_FAKE_RUNTIME", "1")

    opt = (os.environ.get("DEVOS_TEST_DATABASE_URL") or "").strip()
    if opt and _is_postgres_url(opt):
        # Explicit live-Postgres test harness
        os.environ["DATABASE_URL"] = opt
        os.environ["REQUIRE_POSTGRES"] = "true"
        return

    # Default isolated SQLite — always reassert for the pytest process default
    os.environ["REQUIRE_POSTGRES"] = "false"
    # Do not clobber an in-test explicit postgres DATABASE_URL
    current = (os.environ.get("DATABASE_URL") or "").strip()
    if current and _is_postgres_url(current) and os.environ.get("DEVOS_ALLOW_LIVE_POSTGRES") == "1":
        os.environ["REQUIRE_POSTGRES"] = "true"
        return
    os.environ["DATABASE_URL"] = opt or _TEST_DB_DEFAULT


# Module import time (before most test module imports)
_apply_test_db_isolation()


def pytest_configure(config) -> None:
    _apply_test_db_isolation()
    config.addinivalue_line(
        "markers", "postgres: requires a live Postgres/Supabase DATABASE_URL"
    )
    config.addinivalue_line(
        "markers", "production_policy: asserts production REQUIRE_POSTGRES rejection rules"
    )


def pytest_runtest_setup(item) -> None:
    """Restore SQLite isolation unless the test opts into live Postgres."""
    if item.get_closest_marker("production_policy"):
        return
    if item.get_closest_marker("postgres"):
        return
    # Always re-assert SQLite isolation for ordinary tests (prevent env leak).
    _apply_test_db_isolation()
    try:
        from core.sync_session import dispose_sync_engine
        dispose_sync_engine()
    except Exception:
        pass
