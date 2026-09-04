"""Ensure account/profile columns exist (SQLite-safe ALTER)."""
from __future__ import annotations

import logging
from sqlalchemy import text

logger = logging.getLogger("devos.account_schema")

_COLUMNS = {
    "role": "VARCHAR(32) DEFAULT 'member'",
    "plan": "VARCHAR(32) DEFAULT 'recruit'",
    "onboarding_status": "VARCHAR(32) DEFAULT 'NOT_STARTED'",
    "display_name": "VARCHAR(128)",
    "preferred_name": "VARCHAR(128)",
    "avatar_url": "VARCHAR(512)",
    "bio": "TEXT",
    "job_title": "VARCHAR(128)",
    "organization": "VARCHAR(128)",
    "timezone": "VARCHAR(64)",
}


async def ensure_account_columns(engine) -> None:
    try:
        async with engine.begin() as conn:
            def _sync(sync_conn):
                rows = sync_conn.execute(text("PRAGMA table_info(users)")).fetchall()
                existing = {r[1] for r in rows}
                for col, decl in _COLUMNS.items():
                    if col not in existing:
                        sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {decl}"))
                        logger.info("added users.%s", col)
            await conn.run_sync(_sync)
    except Exception as e:
        logger.warning("ensure_account_columns: %s", e)
