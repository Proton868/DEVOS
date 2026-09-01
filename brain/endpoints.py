"""
DevOS — Custom Endpoint Manager
Allows adding ANY OpenAI-compatible or Ollama-compatible endpoint:
  - Hugging Face Inference Endpoints / Serverless Inference API
  - Your own VPS running vLLM / text-generation-webui / LM Studio
  - Home LLM server (Ollama, LocalAI, llama.cpp server)
  - Any other OpenAI-API-compatible service (Groq, Together, Fireworks, etc.)

Endpoints are stored in the database, not hardcoded — fully user-managed.
"""

import logging
from typing import Optional
import httpx

logger = logging.getLogger("devos.endpoints")


class CustomEndpoint:
    """Represents one user-configured LLM endpoint."""

    def __init__(self, id: str, name: str, base_url: str,
                 api_key: str = "", api_format: str = "openai",
                 default_model: str = "", headers: Optional[dict] = None,
                 enabled: bool = True):
        self.id = id
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_format = api_format  # "openai" | "ollama"
        self.default_model = default_model
        self.headers = headers or {}
        self.enabled = enabled

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "base_url": self.base_url,
            "api_format": self.api_format, "default_model": self.default_model,
            "enabled": self.enabled,
            "has_key": bool(self.api_key),
        }


class CustomEndpointClient:
    """
    Calls any OpenAI-compatible or Ollama-compatible endpoint.
    Used by BrainLLM as an additional provider type: "custom:<endpoint_id>"
    """

    def __init__(self, endpoint: CustomEndpoint):
        self.endpoint = endpoint
        self._http = httpx.AsyncClient(timeout=120.0)

    async def chat(self, messages: list[dict], model: Optional[str] = None,
                   temperature: float = 0.1, **kwargs) -> str:
        model = model or self.endpoint.default_model

        if self.endpoint.api_format == "ollama":
            return await self._ollama_chat(messages, model, temperature)
        else:
            return await self._openai_chat(messages, model, temperature)

    async def _openai_chat(self, messages: list[dict], model: str,
                            temperature: float) -> str:
        url = f"{self.endpoint.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", **self.endpoint.headers}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"

        payload = {"model": model, "messages": messages, "temperature": temperature}
        r = await self._http.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

    async def _ollama_chat(self, messages: list[dict], model: str,
                           temperature: float) -> str:
        url = f"{self.endpoint.base_url}/api/chat"
        headers = {"Content-Type": "application/json", **self.endpoint.headers}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"

        payload = {
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature},
        }
        r = await self._http.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def list_models(self) -> list[dict]:
        try:
            if self.endpoint.api_format == "ollama":
                r = await self._http.get(f"{self.endpoint.base_url}/api/tags")
                r.raise_for_status()
                return [{"id": m["name"], "name": m["name"]}
                        for m in r.json().get("models", [])]
            else:
                headers = {}
                if self.endpoint.api_key:
                    headers["Authorization"] = f"Bearer {self.endpoint.api_key}"
                r = await self._http.get(f"{self.endpoint.base_url}/models", headers=headers)
                r.raise_for_status()
                return [{"id": m["id"], "name": m.get("id")}
                        for m in r.json().get("data", [])]
        except Exception as e:
            logger.warning(f"List models failed for {self.endpoint.name}: {e}")
            return []

    async def test_connection(self) -> tuple[bool, str]:
        """Quick health check — try a minimal chat request."""
        try:
            resp = await self.chat(
                [{"role": "user", "content": "Reply with just: OK"}],
                model=self.endpoint.default_model,
            )
            return True, resp[:100]
        except Exception as e:
            return False, str(e)

    async def close(self):
        await self._http.aclose()


class EndpointRegistry:
    """
    Manages all custom endpoints. Backed by SQLite (data/endpoints.db).
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        import sqlite3
        from pathlib import Path
        db_path = Path("data/endpoints.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS endpoints (
                id            TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL,
                name          TEXT NOT NULL,
                base_url      TEXT NOT NULL,
                api_key       TEXT DEFAULT '',
                api_format    TEXT DEFAULT 'openai',
                default_model TEXT DEFAULT '',
                headers       TEXT DEFAULT '{}',
                enabled       INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now'))
            );
        """)
        self._db.commit()

    def add(self, user_id: str, name: str, base_url: str,
            api_key: str = "", api_format: str = "openai",
            default_model: str = "", headers: Optional[dict] = None) -> str:
        import uuid, json
        eid = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO endpoints (id,user_id,name,base_url,api_key,api_format,default_model,headers) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eid, user_id, name, base_url, api_key, api_format,
             default_model, json.dumps(headers or {}))
        )
        self._db.commit()
        return eid

    def list_for_user(self, user_id: str) -> list[CustomEndpoint]:
        import json
        cur = self._db.execute(
            "SELECT id,name,base_url,api_key,api_format,default_model,headers,enabled "
            "FROM endpoints WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        )
        result = []
        for row in cur.fetchall():
            result.append(CustomEndpoint(
                id=row[0], name=row[1], base_url=row[2], api_key=row[3],
                api_format=row[4], default_model=row[5],
                headers=json.loads(row[6] or "{}"), enabled=bool(row[7]),
            ))
        return result

    def get(self, endpoint_id: str) -> Optional[CustomEndpoint]:
        import json
        cur = self._db.execute(
            "SELECT id,name,base_url,api_key,api_format,default_model,headers,enabled "
            "FROM endpoints WHERE id=?", (endpoint_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return CustomEndpoint(
            id=row[0], name=row[1], base_url=row[2], api_key=row[3],
            api_format=row[4], default_model=row[5],
            headers=json.loads(row[6] or "{}"), enabled=bool(row[7]),
        )

    def delete(self, endpoint_id: str, user_id: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM endpoints WHERE id=? AND user_id=?", (endpoint_id, user_id)
        )
        self._db.commit()
        return cur.rowcount > 0

    def update(self, endpoint_id: str, user_id: str, **fields) -> bool:
        if not fields:
            return False
        allowed = {"name", "base_url", "api_key", "api_format",
                   "default_model", "enabled"}
        sets = []
        vals = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False
        vals.extend([endpoint_id, user_id])
        cur = self._db.execute(
            f"UPDATE endpoints SET {', '.join(sets)} WHERE id=? AND user_id=?",
            vals
        )
        self._db.commit()
        return cur.rowcount > 0
