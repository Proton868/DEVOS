#!/usr/bin/env python3
"""Apply supabase/migrations/*.sql in lexical order against DATABASE_URL.

Uses direct synchronous psycopg (no SQLAlchemy engine / greenlet).

- Idempotent SQL (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS) is preferred.
- Does not print credentials.
- Does not run destructive rollbacks.
- Records applied filenames in schema_migrations only after successful apply.
- Forward discovery NEVER includes rollback/seed/backup files (*.down.sql, etc.).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
MIG_DIR = ROOT / "supabase" / "migrations"

# Rollback / non-forward suffixes. Matched against the full filename (lowercased).
_NON_FORWARD_SUFFIXES = (
    ".down.sql",
    ".rollback.sql",
    ".revert.sql",
    ".bak.sql",
    ".backup.sql",
    ".seed.sql",
)

# Forward files: timestamp prefix + descriptive slug + .sql
_FORWARD_NAME_RE = re.compile(
    r"^\d{8,14}_[A-Za-z0-9][A-Za-z0-9_-]*\.sql$"
)

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def is_forward_migration_name(name: str) -> bool:
    """True only for forward schema migrations.

    Excludes rollback companions (*.down.sql), backups, seeds, hidden files,
    and anything that does not follow the timestamped migration naming rule.
    """
    base = Path(name).name
    if not base or base.startswith("."):
        return False
    lower = base.lower()
    if any(lower.endswith(sfx) for sfx in _NON_FORWARD_SUFFIXES):
        return False
    if ".down." in lower:
        return False
    return bool(_FORWARD_NAME_RE.match(base))


def list_forward_migrations(directory: Path) -> list[Path]:
    """Return forward migration files in lexical apply order.

    Never includes *.down.sql or other non-forward SQL, even if they sit
    next to a valid forward file in supabase/migrations/.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    files = [
        p
        for p in directory.iterdir()
        if p.is_file() and is_forward_migration_name(p.name)
    ]
    return sorted(files, key=lambda p: p.name)


def normalize_database_url(url: str) -> str:
    """Convert SQLAlchemy-style Postgres URLs to libpq/psycopg form."""
    u = (url or "").strip()
    for prefix in (
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgresql+asyncpg://",
        "postgres+psycopg://",
        "postgres+psycopg2://",
        "postgres+asyncpg://",
    ):
        if u.lower().startswith(prefix):
            # Preserve original scheme case only for replacement of dialect suffix
            rest = u[len(prefix) :]
            return "postgresql://" + rest
    return u


def _split_sql(sql: str) -> list[str]:
    """Split on semicolons outside dollar-quoted blocks (simple)."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_dollar = False
    while i < len(sql):
        if sql[i : i + 2] == "$$":
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


def _is_postgres_url(url: str) -> bool:
    low = (url or "").strip().lower()
    return low.startswith("postgresql://") or low.startswith("postgres://")


def _apply_sql_file(cur, sql: str) -> None:
    """Execute migration SQL: whole file first, then statement-split fallback."""
    try:
        cur.execute(sql)
        return
    except Exception:
        pass
    for stmt in _split_sql(sql):
        body = "\n".join(
            ln
            for ln in stmt.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        )
        if not body.strip():
            continue
        cur.execute(stmt)


def apply_migrations(
    url: str,
    mig_dir: Optional[Path] = None,
) -> tuple[int, int]:
    """Apply pending *forward* migration files. Returns (applied, skipped).

    A failed migration is not recorded in schema_migrations; the transaction
    for that file is rolled back.

    Rollback files (*.down.sql) are never selected, even if already present
    in schema_migrations from a historical accidental apply.
    """
    import psycopg

    dsn = normalize_database_url(url)
    directory = Path(mig_dir) if mig_dir is not None else MIG_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"missing migration directory: {directory}")

    files = list_forward_migrations(directory)
    if not files:
        return 0, 0

    applied = 0
    skipped = 0
    conn = psycopg.connect(dsn, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(_TRACKING_DDL)
            conn.commit()

            for path in files:
                name = path.name
                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE filename = %s",
                    (name,),
                )
                if cur.fetchone():
                    print("skip", name)
                    skipped += 1
                    continue

                sql = path.read_text(encoding="utf-8")
                try:
                    _apply_sql_file(cur, sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (name,),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                print("applied", name)
                applied += 1
    finally:
        conn.close()

    return applied, skipped


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

    dsn = normalize_database_url(url)
    if not _is_postgres_url(dsn):
        print(
            "ERROR: Postgres/Supabase URL required (got non-postgres after normalize)",
            file=sys.stderr,
        )
        return 1

    try:
        applied, skipped = apply_migrations(url)
    except Exception as e:
        print("ERROR:", type(e).__name__ + ":", e, file=sys.stderr)
        return 1

    print("migrations applied=%s skipped=%s" % (applied, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
