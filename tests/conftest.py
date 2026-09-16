"""Pytest process isolation from production .env Postgres policy.

Production application code keeps REQUIRE_POSTGRES=true fail-closed.
This file only affects the pytest process so local/CI unit tests can use
an isolated SQLite engine without inheriting production DATABASE_URL from .env.

Override with:
  DEVOS_TEST_DATABASE_URL=postgresql+asyncpg://...  (optional real Postgres tests)

Tests that assert SQLite rejection must monkeypatch REQUIRE_POSTGRES=true
and call ops.env_validate.validate() (or construct Settings) without relying
on the process default engine.
"""
from __future__ import annotations

import os
from pathlib import Path

_TEST_DB_DEFAULT = "sqlite+aiosqlite:///./data/pytest_isolated.db"


def _apply_test_db_isolation() -> None:
    """Force test-process DB policy. Safe to call multiple times."""
    os.environ["REQUIRE_POSTGRES"] = "false"
    test_db = (os.environ.get("DEVOS_TEST_DATABASE_URL") or "").strip() or _TEST_DB_DEFAULT
    os.environ["DATABASE_URL"] = test_db
    os.environ.setdefault("DEVOS_ORCH_FAKE_RUNTIME", "1")
    os.environ.setdefault("DEVOS_ALLOW_FAKE_RUNTIME", "1")
    Path("data").mkdir(exist_ok=True)


# Module import time (before test module imports)
_apply_test_db_isolation()


def pytest_configure(config) -> None:
    """Re-assert isolation after plugins load (guards against env pollution)."""
    _apply_test_db_isolation()


def pytest_runtest_setup(item) -> None:
    """Keep isolation unless the test opts into production-policy assertions."""
    # Do not override if test explicitly needs REQUIRE_POSTGRES=true via marker
    if item.get_closest_marker("production_policy"):
        return
    # If a prior test cleared env, restore isolation for DB-touching tests
    if os.environ.get("REQUIRE_POSTGRES", "").lower() not in ("0", "false", "no"):
        # Only force false when DATABASE_URL is sqlite (test durability path)
        url = (os.environ.get("DATABASE_URL") or "").lower()
        if url.startswith("sqlite") or not url:
            _apply_test_db_isolation()
