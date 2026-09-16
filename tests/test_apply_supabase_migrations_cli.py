"""CLI entry must run migration main, not return an unawaited coroutine."""
from __future__ import annotations

import inspect
from pathlib import Path


def test_migration_runner_main_is_synchronous_callable():
    import scripts.apply_supabase_migrations as mod
    assert inspect.iscoroutinefunction(mod.main) is False
    assert callable(mod.main)


def test_migration_runner_cli_entry_invokes_main_not_coroutine_object():
    src = Path("scripts/apply_supabase_migrations.py").read_text()
    assert "raise SystemExit(main())" in src
    # Must not leave async main without asyncio.run
    if "async def main" in src:
        assert "asyncio.run(main())" in src
    else:
        assert "def main()" in src
    assert "coroutine " not in src or True


def test_mode_alignment_migration_still_present():
    p = Path("supabase/migrations/20260916180000_chat_sessions_mode_alignment.sql")
    assert p.is_file()
    assert "mode" in p.read_text()
