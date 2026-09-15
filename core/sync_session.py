"""Sync SQLAlchemy session for modules with sync APIs — same engine URL as async SoT."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_engine = None
_Session = None


def _sync_url(url: str) -> str:
    u = url or ""
    return (
        u.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql+psycopg2://", "postgresql+psycopg://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )


def get_sync_session():
    global _engine, _Session
    from core.config import settings

    url = _sync_url(settings.DATABASE_URL or "")
    require_pg = bool(getattr(settings, "REQUIRE_POSTGRES", True))
    low = url.lower()
    if require_pg and (low.startswith("sqlite") or not low.startswith("postgres")):
        raise RuntimeError(
            "Sync durable stores require Postgres when REQUIRE_POSTGRES=true "
            f"(url dialect forbidden). SQLite production authority is forbidden."
        )
    if _engine is None or str(_engine.url) != url:
        _engine = create_engine(url, future=True)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _Session()


def store_backend() -> str:
    from core.database import engine

    name = (engine.dialect.name or "").lower()
    if name in ("postgresql", "postgres"):
        return "postgres"
    if name.startswith("sqlite"):
        return "sqlite"
    return name or "unknown"
