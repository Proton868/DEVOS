"""Sync SQLAlchemy session for modules with sync APIs — same engine URL as async SoT.

Lifecycle rules:
- One process-wide sync engine per resolved DATABASE_URL.
- Never compare against str(engine.url) (password is masked → false mismatch → leak).
- Dispose the previous engine before creating a replacement.
- Sessions must be closed; prefer ``with get_sync_session() as s:``.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("devos.sync_session")

_lock = threading.RLock()
_engine: Optional[Engine] = None
_Session: Optional[sessionmaker] = None
# Canonical URL string used to build _engine (never str(engine.url) — secrets masked)
_engine_url: Optional[str] = None


def _sync_url(url: str) -> str:
    u = url or ""
    return (
        u.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql+psycopg2://", "postgresql+psycopg://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )


def dispose_sync_engine() -> None:
    """Dispose the process-wide sync engine and clear factories (tests / URL change)."""
    global _engine, _Session, _engine_url
    with _lock:
        eng = _engine
        _engine = None
        _Session = None
        _engine_url = None
        if eng is not None:
            try:
                eng.dispose()
            except Exception as e:
                logger.warning("sync engine dispose failed: %s", type(e).__name__)


def get_sync_engine() -> Engine:
    """Return the shared sync engine, creating it if needed."""
    global _engine, _Session, _engine_url
    from core.config import settings

    url = _sync_url(settings.DATABASE_URL or "")
    require_pg = bool(getattr(settings, "REQUIRE_POSTGRES", True))
    low = url.lower()

    if require_pg and (low.startswith("sqlite") or not low.startswith("postgres")):
        raise RuntimeError(
            "Sync durable stores require Postgres when REQUIRE_POSTGRES=true "
            "(url dialect forbidden). SQLite production authority is forbidden."
        )

    with _lock:
        if _engine is not None and _engine_url == url:
            return _engine

        # URL changed or first create — dispose previous pool first
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception as e:
                logger.warning("sync engine dispose on replace failed: %s", type(e).__name__)
            _engine = None
            _Session = None
            _engine_url = None

        _engine = create_engine(
            url,
            future=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=30,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        _Session = sessionmaker(_engine, expire_on_commit=False, autoflush=False)
        _engine_url = url
        return _engine


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """Yield a short-lived Session; always close (return connection to pool)."""
    get_sync_engine()
    assert _Session is not None
    session: Session = _Session()
    try:
        yield session
    finally:
        try:
            session.close()
        except Exception as e:
            logger.warning("sync session close failed: %s", type(e).__name__)


def store_backend() -> str:
    """Dialect of the authoritative store (prefer sync engine if initialized)."""
    with _lock:
        if _engine is not None:
            name = (_engine.dialect.name or "").lower()
            if name in ("postgresql", "postgres"):
                return "postgres"
            if name.startswith("sqlite"):
                return "sqlite"
            return name or "unknown"
    try:
        from core.database import engine as async_engine

        name = (async_engine.dialect.name or "").lower()
        if name in ("postgresql", "postgres"):
            return "postgres"
        if name.startswith("sqlite"):
            return "sqlite"
        return name or "unknown"
    except Exception:
        return "unknown"
