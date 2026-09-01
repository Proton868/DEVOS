#!/usr/bin/env python3
"""Production readiness gate for DevOS.

Outcomes per check: PASS | WARN | FAIL
Overall: READY | BLOCKED

Exit 0 only when no FAIL items.
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Check:
    def __init__(self, name: str):
        self.name = name
        self.status = "PASS"
        self.detail = ""

    def pass_(self, detail=""):
        self.status, self.detail = "PASS", detail

    def warn(self, detail):
        self.status, self.detail = "WARN", detail

    def fail(self, detail):
        self.status, self.detail = "FAIL", detail


async def main() -> int:
    checks: list[Check] = []

    def add(name: str) -> Check:
        c = Check(name)
        checks.append(c)
        return c

    # Fail inject must not be on in production
    c = add("Failure injection disabled")
    if os.environ.get("DEVOS_FAIL_INJECT"):
        c.fail(f"DEVOS_FAIL_INJECT={os.environ.get('DEVOS_FAIL_INJECT')}")
    else:
        c.pass_()

    c = add("Secret configuration")
    js = (os.environ.get("JWT_SECRET") or os.environ.get("SECRET_KEY") or "").strip()
    if not js or js in ("change-me", "secret", "devos-dev-token-secret"):
        c.fail("JWT_SECRET missing or insecure default")
    else:
        c.pass_("set")

    c = add("Database")
    try:
        from core.database import init_db, engine
        from sqlalchemy import text
        await init_db()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        c.pass_("reachable")
    except Exception as e:
        c.fail(str(e))

    c = add("Required job reliability columns")
    try:
        from core.database import engine
        from sqlalchemy import text
        missing = []
        async with engine.connect() as conn:
            for col in ("idempotency_key", "lease_expires_at", "request_id", "correlation"):
                try:
                    await conn.execute(text(f"SELECT {col} FROM execution_jobs LIMIT 0"))
                except Exception:
                    missing.append(col)
        if missing:
            c.fail(f"missing columns: {missing} — run scripts/migrate_agency_schema.py")
        else:
            c.pass_()
        await engine.dispose()
    except Exception as e:
        c.fail(str(e))

    c = add("Governance freeze / capability registry")
    try:
        from governance.capability_registry import CapabilityRegistry
        if CapabilityRegistry().get("ucip:package.install") is None:
            c.fail("ucip:package.install not registered")
        else:
            c.pass_("package.install present")
    except Exception as e:
        c.fail(str(e))

    c = add("Queue recovery API")
    try:
        from workers.job_queue import recover_stale_leases, complete, claim_next
        assert callable(recover_stale_leases) and callable(complete)
        c.pass_()
    except Exception as e:
        c.fail(str(e))

    c = add("Side-effect UNKNOWN semantics")
    try:
        from governance.side_effects import EffectOutcome, should_retry_job_after_effect, SideEffectRecord
        rec = SideEffectRecord("op", "t", "a", "k", outcome=EffectOutcome.UNKNOWN)
        assert should_retry_job_after_effect(rec) is False
        c.pass_()
    except Exception as e:
        c.fail(str(e))

    c = add("Redis / multi-node quota")
    multi = os.environ.get("DEVOS_MULTI_NODE", "").lower() in ("1", "true", "yes")
    has_redis = bool(os.environ.get("REDIS_URL") or os.environ.get("DEVOS_REDIS_URL"))
    if multi and not has_redis:
        c.fail("DEVOS_MULTI_NODE set but REDIS_URL missing — quotas would fail closed or drift")
    elif not has_redis:
        c.warn("REDIS_URL not set — single-node memory quotas")
    else:
        try:
            from governance.reliability import check_quota_async
            d = await check_quota_async("__checklist__", "max_jobs_per_hour", increment=0)
            c.pass_(f"backend={d.backend}")
        except Exception as e:
            c.warn(str(e))

    c = add("Backup tooling present")
    root = Path(__file__).resolve().parents[1]
    if (root / "scripts" / "backup.py").exists() and (root / "scripts" / "restore.py").exists():
        c.pass_()
    else:
        c.fail("scripts/backup.py or restore.py missing")

    # Report
    print("=== DevOS production checklist ===")
    for ch in checks:
        print(f"[{ch.status:4}] {ch.name}: {ch.detail or ''}".rstrip())
    fails = [ch for ch in checks if ch.status == "FAIL"]
    warns = [ch for ch in checks if ch.status == "WARN"]
    if fails:
        print(f"\nDEPLOYMENT: BLOCKED ({len(fails)} FAIL, {len(warns)} WARN)")
        return 1
    if warns:
        print(f"\nDEPLOYMENT: READY WITH WARNINGS ({len(warns)} WARN)")
        return 0
    print("\nDEPLOYMENT: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
