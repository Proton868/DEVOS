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


    async def get_entity(self, entity_id: str, user_id: str) -> Optional[dict]:
        """Return entity only if owned by user_id (IDOR-safe)."""
        self.init_sync()
        from core.database import AsyncSessionLocal, KnowledgeEntity

        async with AsyncSessionLocal() as db:
            r = await db.get(KnowledgeEntity, entity_id)
            if not r or r.user_id != user_id:
                return None
            return {
                "id": r.id,
                "name": r.name,
                "entity_type": r.entity_type,
                "properties": r.properties,
                "user_id": r.user_id,
            }

    async def find_entities(
        self,
        user_id: str,
        entity_type: Optional[str] = None,
        name_contains: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        self.init_sync()
        from core.database import AsyncSessionLocal, KnowledgeEntity
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            stmt = select(KnowledgeEntity).where(KnowledgeEntity.user_id == user_id)
            if entity_type:
                stmt = stmt.where(KnowledgeEntity.entity_type == entity_type)
            if name_contains:
                stmt = stmt.where(KnowledgeEntity.name.ilike(f"%{name_contains}%"))
            stmt = stmt.limit(limit)
            rows = (await db.execute(stmt)).scalars().all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "entity_type": r.entity_type,
                    "properties": r.properties,
                }
                for r in rows
            ]

    async def get_related(
        self,
        entity_id: str,
        user_id: str,
        relation_type: Optional[str] = None,
        direction: str = "both",
        depth: int = 1,
    ) -> list[dict]:
        """Related entities for an owned entity only."""
        self.init_sync()
        from core.database import AsyncSessionLocal, KnowledgeEntity, KnowledgeRelationship
        from sqlalchemy import select, or_

        async with AsyncSessionLocal() as db:
            root = await db.get(KnowledgeEntity, entity_id)
            if not root or root.user_id != user_id:
                return []
            stmt = select(KnowledgeRelationship).where(
                KnowledgeRelationship.user_id == user_id
            )
            if direction == "out":
                stmt = stmt.where(KnowledgeRelationship.from_entity_id == entity_id)
            elif direction == "in":
                stmt = stmt.where(KnowledgeRelationship.to_entity_id == entity_id)
            else:
                stmt = stmt.where(
                    or_(
                        KnowledgeRelationship.from_entity_id == entity_id,
                        KnowledgeRelationship.to_entity_id == entity_id,
                    )
                )
            if relation_type:
                stmt = stmt.where(KnowledgeRelationship.rel_type == relation_type)
            rows = (await db.execute(stmt.limit(100))).scalars().all()
            return [
                {
                    "id": r.id,
                    "from_entity_id": r.from_entity_id,
                    "to_entity_id": r.to_entity_id,
                    "rel_type": r.rel_type,
                }
                for r in rows
            ]

    async def stats(self, user_id: str) -> dict:
        self.init_sync()
        ents = await self.list_entities(user_id, limit=5000)
        return {"entity_count": len(ents), "user_id": user_id, "backend": self.backend}
