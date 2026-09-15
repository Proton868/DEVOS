"""
DevOS Memory Store — Postgres/Supabase single source of truth.

When REQUIRE_POSTGRES=true, SQLite is forbidden (fail closed).
WorkingMemory remains an in-process cache only (see memory/working.py).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.config import settings

logger = logging.getLogger("devos.memory")


def _dialect_name() -> str:
    try:
        from core.database import engine

        return (engine.dialect.name or "").lower()
    except Exception:
        return "unknown"


class MemoryStore:
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    async def init(self):
        if self._initialized:
            return

        require_pg = bool(getattr(settings, "REQUIRE_POSTGRES", True))
        dialect = _dialect_name()

        if require_pg:
            if dialect not in ("postgresql", "postgres"):
                raise RuntimeError(
                    "MemoryStore requires Postgres when REQUIRE_POSTGRES=true "
                    f"(engine dialect={dialect!r}). SQLite memory is forbidden."
                )
            self._backend = "postgres"
            logger.info("✅ Memory: Postgres (SoT)")
            self._initialized = True
            return

        # Test-only path: allow sqlite engine under REQUIRE_POSTGRES=false
        if dialect.startswith("sqlite"):
            self._backend = "sqlite"
            logger.warning("Memory: SQLite (REQUIRE_POSTGRES=false — test only)")
            self._initialized = True
            return

        if dialect in ("postgresql", "postgres"):
            self._backend = "postgres"
            self._initialized = True
            return

        raise RuntimeError(f"MemoryStore: unsupported database dialect {dialect!r}")

    @property
    def backend(self) -> str:
        return getattr(self, "_backend", "uninitialized")

    async def _embed(self, text: str) -> Optional[list]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{settings.OLLAMA_HOST.rstrip('/')}/api/embeddings",
                    json={"model": "nomic-embed-text", "prompt": text},
                )
                r.raise_for_status()
                return r.json().get("embedding")
        except Exception:
            return None

    async def save(
        self,
        user_id: str,
        role: str,
        content: str,
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        kind: str = "episodic",
        tenant_id: Optional[str] = None,
    ) -> str:
        if not self._initialized:
            await self.init()
        if not user_id:
            raise ValueError("user_id required")

        from core.database import AsyncSessionLocal, MemoryRecord, gen_id, utcnow_naive

        mem_id = gen_id()
        async with AsyncSessionLocal() as db:
            db.add(
                MemoryRecord(
                    id=mem_id,
                    user_id=user_id,
                    session_id=session_id or "",
                    role=role,
                    content=content,
                    meta=metadata or {},
                    kind=kind or "episodic",
                    tenant_id=tenant_id,
                    created_at=utcnow_naive(),
                )
            )
            await db.commit()
        return mem_id

    async def save_learning(
        self,
        user_id: str,
        lesson: str,
        source: str,
        metadata: Optional[dict] = None,
    ) -> str:
        meta = dict(metadata or {})
        meta["source"] = source
        return await self.save(
            user_id=user_id,
            role="system",
            content=lesson,
            metadata=meta,
            kind="learning",
        )

    async def recall(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        kind: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> list[dict]:
        if not self._initialized:
            await self.init()
        if not user_id:
            return []

        from core.database import AsyncSessionLocal, MemoryRecord
        from sqlalchemy import select, desc

        async with AsyncSessionLocal() as db:
            stmt = select(MemoryRecord).where(MemoryRecord.user_id == user_id)
            if kind:
                stmt = stmt.where(MemoryRecord.kind == kind)
            if tenant_id:
                stmt = stmt.where(MemoryRecord.tenant_id == tenant_id)
            if query:
                # ilike is Postgres; LIKE is portable for sqlite tests
                from core.database import engine
                if engine.dialect.name.startswith("sqlite"):
                    stmt = stmt.where(MemoryRecord.content.like(f"%{query}%"))
                else:
                    stmt = stmt.where(MemoryRecord.content.ilike(f"%{query}%"))
            stmt = stmt.order_by(desc(MemoryRecord.created_at)).limit(limit)
            rows = (await db.execute(stmt)).scalars().all()
            return [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "session_id": r.session_id,
                    "role": r.role,
                    "content": r.content,
                    "metadata": r.meta or {},
                    "kind": r.kind,
                    "tenant_id": r.tenant_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    async def get_history(
        self, user_id: str, session_id: str, limit: int = 40
    ) -> list[dict]:
        if not self._initialized:
            await self.init()
        if not user_id:
            return []

        from core.database import AsyncSessionLocal, MemoryRecord
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            stmt = (
                select(MemoryRecord)
                .where(
                    MemoryRecord.user_id == user_id,
                    MemoryRecord.session_id == session_id,
                )
                .order_by(MemoryRecord.created_at)
                .limit(limit)
            )
            rows = (await db.execute(stmt)).scalars().all()
            return [
                {
                    "role": r.role,
                    "content": r.content,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    async def delete(self, user_id: str, mem_id: str) -> bool:
        """Owner-scoped delete — user_id must match row."""
        if not self._initialized:
            await self.init()
        from core.database import AsyncSessionLocal, MemoryRecord
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            res = await db.execute(
                delete(MemoryRecord).where(
                    MemoryRecord.id == mem_id,
                    MemoryRecord.user_id == user_id,
                )
            )
            await db.commit()
            return (res.rowcount or 0) > 0

    async def update(self, user_id: str, mem_id: str, content: str) -> bool:
        if not self._initialized:
            await self.init()
        from core.database import AsyncSessionLocal, MemoryRecord

        async with AsyncSessionLocal() as db:
            row = await db.get(MemoryRecord, mem_id)
            if not row or row.user_id != user_id:
                return False
            row.content = content
            await db.commit()
            return True
