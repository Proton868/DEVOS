#!/usr/bin/env python3
"""Repository-level deployment simulation (no production mutation).

Validates that the checkout contains the deploy chain:
  install assets → env validation → migrations assets → systemd template →
  health route → auth → Nuha/A2A/Ponytail modules → outbox/saga → evidence hooks

Does not print secrets. Does not connect to production unless DATABASE_URL is set
and --live-db is passed.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ok(msg: str) -> None:
    print("OK ", msg)


def fail(msg: str) -> None:
    print("FAIL", msg)
    raise SystemExit(1)


def check_files() -> None:
    required = [
        "ops/install.sh",
        "ops/update.sh",
        "ops/verify.sh",
        "ops/apply_migrations.sh",
        "ops/install_systemd.sh",
        "ops/env_validate.py",
        "ops/templates/devos.service.in",
        "scripts/apply_supabase_migrations.py",
        "supabase/migrations",
        "requirements.txt",
        ".env.example",
        "app.py",
        "core/config.py",
        "core/database.py",
        "core/sync_session.py",
        "core/secrets_redact.py",
        "brain/a2a.py",
        "brain/nuha_bridge.py",
        "api/routes/ponytail.py",
        "api/routes/health.py",
        "api/routes/chat.py",
        "execution/outbox.py",
        "execution/saga.py",
    ]
    for rel in required:
        p = ROOT / rel
        if not p.exists():
            fail("missing %s" % rel)
    ok("required deploy/runtime files present")


def check_imports() -> None:
    sys.path.insert(0, str(ROOT))
    modules = [
        "app",
        "core.config",
        "core.database",
        "core.sync_session",
        "core.secrets_redact",
        "brain.a2a",
        "brain.nuha_bridge",
        "execution.outbox",
        "execution.saga",
    ]
    for name in modules:
        try:
            importlib.import_module(name)
            ok("import %s" % name)
        except Exception as e:
            fail("import %s: %s" % (name, type(e).__name__))


def check_architecture_invariants() -> None:
    sys.path.insert(0, str(ROOT))
    from core.config import settings

    if (settings.DEFAULT_PROVIDER or "").lower() != "omniroute":
        fail("DEFAULT_PROVIDER must default to omniroute")
    ok("DEFAULT_PROVIDER=omniroute")
    if not getattr(settings, "AUTH_ENABLED", True):
        # settings may be overridden by env in CI
        if os.environ.get("AUTH_ENABLED", "true").lower() in ("0", "false", "no"):
            ok("AUTH_ENABLED overridden for test env")
        else:
            fail("AUTH_ENABLED false in settings without test override")
    else:
        ok("AUTH_ENABLED true")
    # Nuha must not be a specialist worker slug
    try:
        from brain.personas import NUHA
        if getattr(NUHA, "agent_slug", None):
            fail("NUHA must not bind agent_slug as specialist worker")
        ok("NUHA orchestrator persona (no agent_slug)")
    except Exception as e:
        fail("personas: %s" % type(e).__name__)
    # A2A envelope kinds include ponytail
    from brain import a2a as a2a_mod
    src = Path(a2a_mod.__file__).read_text()
    if "ponytail" not in src.lower():
        fail("A2A module missing ponytail message kinds")
    ok("A2A includes ponytail message kinds")


def check_migrations() -> None:
    files = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    if not files:
        fail("no supabase migrations")
    ok("%d migration files" % len(files))


def check_env_example() -> None:
    text = (ROOT / ".env.example").read_text()
    for key in ("DATABASE_URL", "JWT_SECRET", "REQUIRE_POSTGRES", "DEFAULT_PROVIDER", "ALLOWED_ORIGINS"):
        if key not in text:
            fail(".env.example missing %s" % key)
    ok(".env.example has required keys")


def maybe_live_db() -> None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        ok("live-db skipped (no DATABASE_URL)")
        return
    if url.lower().startswith("sqlite"):
        ok("live-db skipped (sqlite)")
        return
    import asyncio
    async def _go():
        from core.database import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
    try:
        asyncio.run(_go())
        ok("live database connectivity")
    except Exception as e:
        fail("live database: %s" % type(e).__name__)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live-db", action="store_true")
    p.add_argument("--skip-imports", action="store_true")
    args = p.parse_args()
    os.chdir(ROOT)
    check_files()
    check_migrations()
    check_env_example()
    if not args.skip_imports:
        check_imports()
        check_architecture_invariants()
    if args.live_db:
        maybe_live_db()
    print("repo_deploy_simulation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
