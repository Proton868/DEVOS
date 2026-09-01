#!/usr/bin/env python3
"""Production readiness checks for DevOS Agency OS.

Run: python scripts/production_checklist.py
Exits non-zero if a hard requirement fails.
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    fails = []
    warns = []

    # Secrets
    if not os.environ.get("JWT_SECRET") and not os.environ.get("SECRET_KEY"):
        warns.append("JWT_SECRET/SECRET_KEY not set (dev default may be used)")
    if (os.environ.get("JWT_SECRET") or "").strip() in ("", "change-me", "secret"):
        fails.append("JWT_SECRET is missing or insecure")

    # DB
    try:
        from core.database import init_db, engine
        from sqlalchemy import text
        await init_db()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        # reliability columns
        async with engine.connect() as conn:
            for col in ("idempotency_key", "lease_expires_at", "request_id", "correlation"):
                try:
                    await conn.execute(text(f"SELECT {col} FROM execution_jobs LIMIT 0"))
                except Exception:
                    # sqlite may not have until migrate
                    warns.append(f"execution_jobs.{col} missing — run migrate_agency_schema")
        await engine.dispose()
    except Exception as e:
        fails.append(f"database: {e}")

    # Governance freeze markers
    try:
        from governance.capability_registry import CapabilityRegistry
        if CapabilityRegistry().get("ucip:package.install") is None:
            fails.append("ucip:package.install not registered")
    except Exception as e:
        fails.append(f"capability registry: {e}")

    try:
        from workers.job_queue import recover_stale_leases, enqueue, complete
        assert callable(recover_stale_leases)
    except Exception as e:
        fails.append(f"job queue: {e}")

    # Redis optional
    if os.environ.get("REDIS_URL") or os.environ.get("DEVOS_REDIS_URL"):
        try:
            from governance.reliability import check_quota_async
            d = await check_quota_async("__health__", "max_jobs_per_hour", increment=0)
            print(f"[ok] quota backend={d.backend}")
        except Exception as e:
            warns.append(f"redis quota: {e}")
    else:
        warns.append("REDIS_URL not set — using in-process quota/queue notify")

    print("=== DevOS production checklist ===")
    for w in warns:
        print(f"[warn] {w}")
    for f in fails:
        print(f"[FAIL] {f}")
    if fails:
        print("RESULT: NOT READY")
        return 1
    print("RESULT: READY (warnings above are non-blocking)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
