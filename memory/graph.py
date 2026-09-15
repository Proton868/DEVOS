"""
Semantic knowledge graph — Postgres SoT (same engine as MemoryStore).

Fail closed when REQUIRE_POSTGRES=true and engine is not Postgres.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from core.config import settings

logger = logging.getLogger("devos.memory.graph")


class KnowledgeGraph:
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def init_sync(self):
        if self._initialized:
            return
        from core.database import engine

        dialect = (engine.dialect.name or "").lower()
        require_pg = bool(getattr(settings, "REQUIRE_POSTGRES", True))
        if require_pg and dialect not in ("postgresql", "postgres"):
            raise RuntimeError(
                f"KnowledgeGraph requires Postgres when REQUIRE_POSTGRES=true (got {dialect})"
            )
        self._backend = "postgres" if dialect.startswith("postgres") else dialect
        self._initialized = True

    @property
    def backend(self) -> str:
        return getattr(self, "_backend", "uninitialized")

    async def add_entity(
        self,
        user_id: str,
        name: str,
        entity_type: str = "concept",
        properties: Optional[dict] = None,
    ) -> str:
        self.init_sync()
        from core.database import AsyncSessionLocal, KnowledgeEntity, gen_id, utcnow_naive

        eid = gen_id()
        async with AsyncSessionLocal() as db:
            db.add(
                KnowledgeEntity(
                    id=eid,
                    user_id=user_id,
                    name=name,
                    entity_type=entity_type,
                    properties=properties or {},
                    created_at=utcnow_naive(),
                )
            )
            await db.commit()
        return eid

    async def add_relationship(
        self,
        user_id: str,
        from_entity_id: str,
        to_entity_id: str,
        rel_type: str = "related_to",
        properties: Optional[dict] = None,
    ) -> str:
        self.init_sync()
        from core.database import (
            AsyncSessionLocal,
            KnowledgeRelationship,
            KnowledgeEntity,
            gen_id,
            utcnow_naive,
        )

        async with AsyncSessionLocal() as db:
            a = await db.get(KnowledgeEntity, from_entity_id)
            b = await db.get(KnowledgeEntity, to_entity_id)
            if not a or not b or a.user_id != user_id or b.user_id != user_id:
                raise PermissionError("entity ownership mismatch")
            rid = gen_id()
            db.add(
                KnowledgeRelationship(
                    id=rid,
                    user_id=user_id,
                    from_entity_id=from_entity_id,
                    to_entity_id=to_entity_id,
                    rel_type=rel_type,
                    properties=properties or {},
                    created_at=utcnow_naive(),
                )
            )
            await db.commit()
            return rid

    async def list_entities(self, user_id: str, limit: int = 50) -> list[dict]:
        self.init_sync()
        from core.database import AsyncSessionLocal, KnowledgeEntity
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(KnowledgeEntity)
                    .where(KnowledgeEntity.user_id == user_id)
                    .limit(limit)
                )
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "entity_type": r.entity_type,
                    "properties": r.properties,
                }
                for r in rows
            ]
