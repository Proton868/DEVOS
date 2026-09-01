"""
Memory — Semantic division, upgraded from flat text to a real graph.

Closes a gap flagged since Session 4: the Semantic `kind` on MemoryStore
holds arbitrary text entries, searchable only by keyword — no actual
entities, no relationships between them, nothing a Worker could traverse
("what do we know that's connected to X?"). This module adds that
structure as a genuine entity/relationship graph.

Scope, stated plainly: this does NOT do automatic entity extraction from
free text via an LLM — that would need a live model call this sandbox
can't reach (same network-allowlist limitation noted in every session
since Session 3), and building an untested extraction pipeline would be
worse than not building one. Instead, this exposes a direct, structured
API — add_entity()/add_relationship() — that a Worker, the Cognitive
System, or a future extraction step can call once entities/relationships
have actually been identified. The graph itself is real and fully
functional; what's deferred is *automatically populating it from prose*.

Backend note, same pattern as communications/bus.py and memory/working.py:
this is SQLite-backed for now (matching what's actually configured and
tested in this environment), with a schema and query interface designed
so a Supabase/Postgres-backed version (real pgvector graph, per the
original master plan) could implement the same interface later without
callers changing.
"""
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.memory.graph")

GRAPH_DB = Path("data/memory.db")  # same database file as memory/store.py —
                                    # one Memory organ, not a second store


class KnowledgeGraph:
    """Singleton, matching the pattern already established for
    WorkingMemory/EventBus/HITLQueue in this codebase."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        GRAPH_DB.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(GRAPH_DB), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                tenant_id   TEXT,
                type        TEXT NOT NULL,
                name        TEXT NOT NULL,
                properties  TEXT DEFAULT '{}',
                created_at  TEXT NOT NULL,
                UNIQUE(user_id, type, name)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_entities_user ON kg_entities(user_id);
            CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(user_id, type);

            CREATE TABLE IF NOT EXISTS kg_relationships (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                from_entity_id  TEXT NOT NULL,
                to_entity_id    TEXT NOT NULL,
                relation_type   TEXT NOT NULL,
                properties      TEXT DEFAULT '{}',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (from_entity_id) REFERENCES kg_entities(id),
                FOREIGN KEY (to_entity_id) REFERENCES kg_entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_rel_from ON kg_relationships(from_entity_id);
            CREATE INDEX IF NOT EXISTS idx_kg_rel_to   ON kg_relationships(to_entity_id);
        """)
        self._db.commit()
        logger.info("✅ Knowledge graph tables ready (kg_entities, kg_relationships)")

    # ── Entities ─────────────────────────────────────────────
    def add_entity(self, user_id: str, entity_type: str, name: str,
                   properties: Optional[dict] = None, tenant_id: Optional[str] = None) -> str:
        """Idempotent by (user_id, type, name) — calling this again for an
        entity that already exists returns the existing id rather than
        creating a duplicate. Properties on a re-add are merged into the
        existing entity, not discarded."""
        existing = self._db.execute(
            "SELECT id, properties FROM kg_entities WHERE user_id=? AND type=? AND name=?",
            (user_id, entity_type, name)
        ).fetchone()
        if existing:
            entity_id, existing_props_json = existing
            if properties:
                merged = {**json.loads(existing_props_json), **properties}
                self._db.execute("UPDATE kg_entities SET properties=? WHERE id=?",
                                 (json.dumps(merged), entity_id))
                self._db.commit()
            return entity_id

        entity_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO kg_entities (id, user_id, tenant_id, type, name, properties, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (entity_id, user_id, tenant_id, entity_type, name,
             json.dumps(properties or {}), datetime.now(timezone.utc).isoformat())
        )
        self._db.commit()
        return entity_id

    def get_entity(self, entity_id: str) -> Optional[dict]:
        row = self._db.execute(
            "SELECT id, user_id, type, name, properties, created_at FROM kg_entities WHERE id=?",
            (entity_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_entity(row)

    def find_entities(self, user_id: str, entity_type: Optional[str] = None,
                      name_contains: Optional[str] = None, limit: int = 50) -> list[dict]:
        sql = "SELECT id, user_id, type, name, properties, created_at FROM kg_entities WHERE user_id=?"
        params = [user_id]
        if entity_type:
            sql += " AND type=?"
            params.append(entity_type)
        if name_contains:
            sql += " AND name LIKE ?"
            params.append(f"%{name_contains}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def delete_entity(self, entity_id: str) -> bool:
        """Also removes every relationship touching this entity — a
        relationship pointing at a deleted entity would be a dangling
        reference, not a meaningful graph edge."""
        self._db.execute("DELETE FROM kg_relationships WHERE from_entity_id=? OR to_entity_id=?",
                         (entity_id, entity_id))
        cur = self._db.execute("DELETE FROM kg_entities WHERE id=?", (entity_id,))
        self._db.commit()
        return cur.rowcount > 0

    # ── Relationships ────────────────────────────────────────
    def add_relationship(self, user_id: str, from_entity_id: str, to_entity_id: str,
                         relation_type: str, properties: Optional[dict] = None) -> str:
        if not self.get_entity(from_entity_id) or not self.get_entity(to_entity_id):
            raise ValueError("Both from_entity_id and to_entity_id must reference existing entities")
        rel_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO kg_relationships (id, user_id, from_entity_id, to_entity_id, relation_type, properties, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rel_id, user_id, from_entity_id, to_entity_id, relation_type,
             json.dumps(properties or {}), datetime.now(timezone.utc).isoformat())
        )
        self._db.commit()
        return rel_id

    def get_related(self, entity_id: str, relation_type: Optional[str] = None,
                    direction: str = "both", depth: int = 1, max_nodes: int = 200) -> list[dict]:
        """Breadth-first traversal outward from entity_id, up to `depth`
        hops. `direction`: "out" (only follow from_entity_id -> to_entity_id),
        "in" (only follow the reverse), or "both". `max_nodes` bounds total
        traversal size regardless of depth, so a densely connected graph
        can't produce an unbounded result — same defensive-bound philosophy
        as cognitive/coordinator.py's dependency-wave execution."""
        if depth < 1:
            return []
        visited = {entity_id}
        frontier = {entity_id}
        results = []

        for _ in range(depth):
            if not frontier or len(results) >= max_nodes:
                break
            next_frontier = set()
            for node_id in frontier:
                edges = self._fetch_edges(node_id, relation_type, direction)
                for edge in edges:
                    other_id = edge["to_entity_id"] if edge["from_entity_id"] == node_id else edge["from_entity_id"]
                    if other_id in visited:
                        continue  # cycle guard — a graph can legitimately have cycles
                    visited.add(other_id)
                    next_frontier.add(other_id)
                    entity = self.get_entity(other_id)
                    if entity:
                        results.append({"entity": entity, "via_relationship": edge})
                    if len(results) >= max_nodes:
                        break
                if len(results) >= max_nodes:
                    break
            frontier = next_frontier

        return results

    def _fetch_edges(self, entity_id: str, relation_type: Optional[str], direction: str) -> list[dict]:
        clauses, params = [], []
        if direction in ("out", "both"):
            clauses.append("from_entity_id=?")
            params.append(entity_id)
        if direction in ("in", "both"):
            clauses.append("to_entity_id=?")
            params.append(entity_id)
        sql = f"SELECT id, from_entity_id, to_entity_id, relation_type, properties, created_at FROM kg_relationships WHERE ({' OR '.join(clauses)})"
        if relation_type:
            sql += " AND relation_type=?"
            params.append(relation_type)
        rows = self._db.execute(sql, params).fetchall()
        return [{"id": r[0], "from_entity_id": r[1], "to_entity_id": r[2],
                 "relation_type": r[3], "properties": json.loads(r[4]), "created_at": r[5]}
                for r in rows]

    def _row_to_entity(self, row) -> dict:
        return {"id": row[0], "user_id": row[1], "type": row[2], "name": row[3],
                "properties": json.loads(row[4]), "created_at": row[5]}

    def stats(self, user_id: str) -> dict:
        entity_count = self._db.execute(
            "SELECT COUNT(*) FROM kg_entities WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        rel_count = self._db.execute(
            "SELECT COUNT(*) FROM kg_relationships WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        return {"entities": entity_count, "relationships": rel_count}

    # ── Name-based convenience API (Session 15) ─────────────────
    # add_entity/add_relationship above are the primitive operations, keyed
    # by entity id — correct for a REST API, awkward for an LLM-driven tool
    # call, since a Brain reasoning about "Alice works_on Agency OS" doesn't
    # know either entity's UUID ahead of time and shouldn't have to look
    # one up in a separate step first. These wrap the primitives with
    # upsert-by-name semantics instead.
    def upsert_relationship(self, user_id: str, from_type: str, from_name: str,
                            to_type: str, to_name: str, relation_type: str,
                            properties: Optional[dict] = None) -> dict:
        """Creates both entities if they don't already exist (idempotent,
        via add_entity's existing dedup) and links them. Returns the
        resolved ids so a caller can see what was actually created/reused."""
        from_id = self.add_entity(user_id, from_type, from_name)
        to_id = self.add_entity(user_id, to_type, to_name)
        rel_id = self.add_relationship(user_id, from_id, to_id, relation_type, properties)
        return {"from_entity_id": from_id, "to_entity_id": to_id, "relationship_id": rel_id}

    def query_by_name(self, user_id: str, name: str, relation_type: Optional[str] = None,
                      direction: str = "both", depth: int = 1) -> dict:
        """Resolves an entity by name (first match) then traverses from it.
        Returns an explicit not-found marker rather than an empty list when
        no entity matches, so a caller (or the Brain reading the tool's
        observation) can tell "nothing connected to X" apart from "X isn't
        in the graph at all"."""
        matches = self.find_entities(user_id, name_contains=name, limit=1)
        if not matches:
            return {"found": False, "entity": None, "related": []}
        entity = matches[0]
        related = self.get_related(entity["id"], relation_type, direction, depth)
        return {"found": True, "entity": entity, "related": related}
