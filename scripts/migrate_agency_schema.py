#!/usr/bin/env python3
"""Idempotent Agency OS schema migration for deploy."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
async def main():
    from core.database import init_db, engine, AsyncSessionLocal
    print("[migrate] init_db...")
    await init_db()
    try:
        from governance.durable_capabilities import load_tenant_capabilities
        async with AsyncSessionLocal() as db:
            n=await load_tenant_capabilities(db)
            print(f"[migrate] loaded {n} durable capabilities")
    except Exception as e:
        print(f"[migrate] durable load skipped: {e}")
    print("[migrate] done"); await engine.dispose()
if __name__=="__main__": asyncio.run(main())
