"""
DevOS Memory Store — SQLite-first, ChromaDB/Supabase optional.
Falls back gracefully if heavy deps not installed.
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import settings

logger = logging.getLogger("devos.memory")

MEMORY_DB = Path("data/memory.db")


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

        # Try Supabase first
        if settings.has_supabase:
            try:
                from supabase import create_client
                self._sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
                self._backend = "supabase"
                logger.info("✅ Memory: Supabase")
                self._initialized = True
                return
            except Exception as e:
                logger.warning(f"Supabase unavailable ({e}), trying ChromaDB…")

        # Try ChromaDB
        try:
            import chromadb
            self._chroma = chromadb.HttpClient(
                host=settings.CHROMADB_HOST,
                port=settings.CHROMADB_PORT,
            )
            self._col = self._chroma.get_or_create_collection("devos")
            self._backend = "chromadb"
            logger.info("✅ Memory: ChromaDB")
            self._initialized = True
            return
        except Exception:
            pass

        # SQLite fallback — always works, zero deps
        self._init_sqlite()
        self._initialized = True

    def _init_sqlite(self):
        MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(MEMORY_DB), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                session_id  TEXT,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mem_user    ON memories(user_id);
            CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session_id);
        """)
        self._migrate_sqlite_divisions()
        self._db.commit()
        self._backend = "sqlite"
        logger.info("✅ Memory: SQLite (lite mode)")

    def _migrate_sqlite_divisions(self):
        """Adds `kind` and `tenant_id` columns to a pre-existing memories
        table without disturbing existing rows — safe to run against a
        database that already has data, every time the app starts.
        `kind` implements the Memory organ's internal division scheme
        (episodic/semantic/vector/working/long_term/tenant/learning) as a
        column on one table, per the master plan's explicit call for these
        to be logical schemas/namespaces rather than separate services on
        the Micro Acer profile. Existing rows default to kind='episodic'
        since that's what this table exclusively held before this change."""
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(memories)")}
        if "kind" not in cols:
            self._db.execute("ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'episodic'")
            logger.info("[memory] migrated: added 'kind' column (existing rows → episodic)")
        if "tenant_id" not in cols:
            self._db.execute("ALTER TABLE memories ADD COLUMN tenant_id TEXT")
            logger.info("[memory] migrated: added 'tenant_id' column")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_mem_tenant ON memories(tenant_id)")

    @property
    def backend(self) -> str:
        return getattr(self, "_backend", "uninitialized")

    async def _embed(self, text: str) -> Optional[list]:
        """Try Ollama embeddings — silently skip if unavailable."""
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

    async def save(self, user_id: str, role: str, content: str,
                   session_id: Optional[str] = None,
                   metadata: Optional[dict] = None,
                   kind: str = "episodic",
                   tenant_id: Optional[str] = None) -> str:
        """kind: one of episodic|semantic|working|long_term|tenant|learning
        (vector isn't a separate kind — it's an access pattern, see recall()).
        Defaults to 'episodic' so every existing caller keeps working
        unchanged."""
        if not self._initialized:
            await self.init()

        mem_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if self._backend == "supabase":
            embedding = await self._embed(content)
            self._sb.table("devos_memories").insert({
                "id": mem_id, "user_id": user_id, "role": role,
                "content": content, "session_id": session_id or "",
                "embedding": embedding, "metadata": metadata or {},
                "kind": kind, "tenant_id": tenant_id,
                "created_at": now,
            }).execute()

        elif self._backend == "chromadb":
            embedding = await self._embed(content)
            self._col.add(
                ids=[mem_id], documents=[content],
                embeddings=[embedding] if embedding else None,
                metadatas=[{"user_id": user_id, "role": role,
                             "session_id": session_id or "", "kind": kind,
                             "tenant_id": tenant_id or "",
                             **{k: str(v) for k, v in (metadata or {}).items()}}],
            )

        else:  # sqlite
            self._db.execute(
                "INSERT INTO memories (id, user_id, session_id, role, content, metadata, kind, tenant_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mem_id, user_id, session_id or "", role, content,
                 json.dumps(metadata or {}), kind, tenant_id, now)
            )
            self._db.commit()

        return mem_id

    async def save_learning(self, user_id: str, lesson: str, source: str,
                            metadata: Optional[dict] = None) -> str:
        """Convenience wrapper for the Learning division — a distilled
        takeaway from a completed task/session, distinct from the full
        execution trace (which stays wherever it already lives, e.g.
        autoresearch's per-session JSONL log). `source` identifies what
        produced the lesson (e.g. 'autoresearch', 'evolution', 'hitl_denial')
        so lessons stay queryable by origin."""
        return await self.save(user_id, role="lesson", content=lesson,
                               kind="learning",
                               metadata={**(metadata or {}), "source": source})

    async def recall(self, user_id: str, query: str,
                     limit: int = 5,
                     session_id: Optional[str] = None,
                     kind: Optional[str] = None,
                     tenant_id: Optional[str] = None) -> list[dict]:
        """kind=None (default) searches across all divisions, exactly as
        this method behaved before divisions existed. Pass kind='learning',
        kind='semantic', etc. to scope the search to one division."""
        if not self._initialized:
            await self.init()

        if self._backend == "supabase":
            embedding = await self._embed(query)
            if embedding:
                try:
                    r = self._sb.rpc("match_memories", {
                        "query_embedding": embedding,
                        "match_user_id": user_id,
                        "match_count": limit,
                    }).execute()
                    return r.data or []
                except Exception:
                    pass
            q = (self._sb.table("devos_memories")
                 .select("*")
                 .eq("user_id", user_id)
                 .ilike("content", f"%{query}%"))
            if kind:
                q = q.eq("kind", kind)
            if tenant_id:
                q = q.eq("tenant_id", tenant_id)
            r = q.limit(limit).execute()
            return r.data or []

        elif self._backend == "chromadb":
            where = {"user_id": user_id}
            if kind:
                where["kind"] = kind
            if tenant_id:
                where["tenant_id"] = tenant_id
            embedding = await self._embed(query)
            kwargs = {"n_results": limit, "where": where}
            if embedding:
                kwargs["query_embeddings"] = [embedding]
            else:
                kwargs["query_texts"] = [query]
            result = self._col.query(**kwargs)
            docs  = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            return [{"content": d, **m} for d, m in zip(docs, metas)]

        else:  # sqlite — simple keyword search
            sql = ("SELECT id, user_id, session_id, role, content, metadata, kind, tenant_id, created_at "
                   "FROM memories WHERE user_id=? AND content LIKE ?")
            params = [user_id, f"%{query}%"]
            if kind:
                sql += " AND kind=?"
                params.append(kind)
            if tenant_id:
                sql += " AND tenant_id=?"
                params.append(tenant_id)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            cur = self._db.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            for r in rows:
                try:
                    r["metadata"] = json.loads(r.get("metadata", "{}"))
                except Exception:
                    pass
            return rows

    async def get_history(self, user_id: str, session_id: str,
                          limit: int = 40) -> list[dict]:
        if not self._initialized:
            await self.init()

        if self._backend == "supabase":
            r = (self._sb.table("devos_memories")
                 .select("role,content,created_at")
                 .eq("user_id", user_id)
                 .eq("session_id", session_id)
                 .order("created_at")
                 .limit(limit)
                 .execute())
            return r.data or []

        elif self._backend == "chromadb":
            result = self._col.query(
                query_texts=[""], n_results=limit,
                where={"user_id": user_id, "session_id": session_id},
            )
            docs  = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            return [{"content": d, "role": m.get("role", "user")}
                    for d, m in zip(docs, metas)]

        else:  # sqlite
            cur = self._db.execute(
                "SELECT role, content, created_at FROM memories "
                "WHERE user_id=? AND session_id=? ORDER BY created_at LIMIT ?",
                (user_id, session_id, limit)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
