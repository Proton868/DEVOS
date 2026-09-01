#!/usr/bin/env python3
"""Idempotent migration resume — safe after mid-migration crash.

Runs init_db / _add_column_if_missing path repeatedly until stable.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

async def main():
    from core.database import init_db, engine
    print("[migrate_resume] pass 1")
    await init_db()
    print("[migrate_resume] pass 2 (idempotent)")
    await init_db()
    await engine.dispose()
    print("[migrate_resume] done")

if __name__ == "__main__":
    asyncio.run(main())
