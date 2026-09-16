#!/usr/bin/env python3
"""Apply supabase/migrations/*.sql in lexical order against DATABASE_URL.

- Idempotent SQL (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS) is preferred.
- Does not print credentials.
- Does not run destructive rollbacks.
- Records applied filenames in schema_migrations when possible.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG_DIR = ROOT / "supabase" / "migrations"


def _split_sql(sql: str) -> list[str]:
    """Split on semicolons outside dollar-quoted blocks (simple)."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_dollar = False
    while i < len(sql):
        if sql[i:i + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt and not stmt.startswith("--"):
                parts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


async def _ensure_tracking(conn) -> None:
    from sqlalchemy import text
    await conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    ))


def main() -> int:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("ERROR: DATABASE_URL required", file=sys.stderr)
        return 1
    low = url.lower()
    if low.startswith("sqlite"):
        print("ERROR: refuse to apply Supabase SQL migrations to SQLite", file=sys.stderr)
        return 1
    if not MIG_DIR.is_dir():
        print("ERROR: missing", MIG_DIR, file=sys.stderr)
        return 1

    files = sorted(MIG_DIR.glob("*.sql"))
    if not files:
        print("No migration files found")
        return 0

    # Prefer sync psycopg for multi-statement DDL reliability
    try:
        from core.sync_session import get_sync_engine, dispose_sync_engine
        from sqlalchemy import text
    except Exception as e:
        print("ERROR: cannot import sync engine:", type(e).__name__, file=sys.stderr)
        return 1

    engine = get_sync_engine()
    applied = 0
    skipped = 0
    try:
        with engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            ))
            for path in files:
                name = path.name
                row = conn.execute(
                    text("SELECT 1 FROM schema_migrations WHERE filename = :f"),
                    {"f": name},
                ).fetchone()
                if row:
                    print("skip", name)
                    skipped += 1
                    continue
                sql = path.read_text()
                # Strip pure comment-only noise; execute whole file when possible
                try:
                    conn.execute(text(sql))
                except Exception:
                    # Fallback: statement split
                    for stmt in _split_sql(sql):
                        # skip empty / comment-only
                        body = "\n".join(
                            ln for ln in stmt.splitlines()
                            if ln.strip() and not ln.strip().startswith("--")
                        )
                        if not body.strip():
                            continue
                        conn.execute(text(stmt))
                conn.execute(
                    text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                    {"f": name},
                )
                print("applied", name)
                applied += 1
    finally:
        dispose_sync_engine()

    print("migrations applied=%s skipped=%s" % (applied, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
