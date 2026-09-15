"""
DevOS — Custom Endpoint Manager

User-managed OpenAI/Ollama-compatible endpoints.
Backed by Postgres/Supabase (CustomEndpointRecord) — NOT data/endpoints.db.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

import httpx

logger = logging.getLogger("devos.endpoints")


class CustomEndpoint:
    """Represents one user-configured LLM endpoint."""

    def __init__(
        self,
        id: str,
        name: str,
        base_url: str,
        api_key: str = "",
        api_format: str = "openai",
        default_model: str = "",
        headers: Optional[dict] = None,
        enabled: bool = True,
        user_id: str = "",
    ):
        self.id = id
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_format = api_format
        self.default_model = default_model
        self.headers = headers or {}
        self.enabled = enabled
        self.user_id = user_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_format": self.api_format,
            "default_model": self.default_model,
            "enabled": self.enabled,
            "has_key": bool(self.api_key),
        }


class CustomEndpointClient:
    def __init__(self, endpoint: CustomEndpoint):
        self.endpoint = endpoint
        self._http = httpx.AsyncClient(timeout=120.0)

    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.1,
        **kwargs,
    ) -> str:
        model = model or self.endpoint.default_model
        if self.endpoint.api_format == "ollama":
            return await self._ollama_chat(messages, model, temperature)
        return await self._openai_chat(messages, model, temperature)

    async def _openai_chat(self, messages: list[dict], model: str, temperature: float) -> str:
        url = f"{self.endpoint.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", **self.endpoint.headers}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"
        payload = {"model": model, "messages": messages, "temperature": temperature}
        r = await self._http.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

    async def _ollama_chat(self, messages: list[dict], model: str, temperature: float) -> str:
        url = f"{self.endpoint.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        r = await self._http.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("message", {}).get("content", "")

    async def close(self):
        await self._http.aclose()


def _sync_url(async_url: str) -> str:
    u = async_url or ""
    return (
        u.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql+psycopg2://", "postgresql+psycopg://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )


class EndpointRegistry:
    """
    Manages custom endpoints. Authoritative store: CustomEndpointRecord (Postgres).
    Sync API preserved for existing callers; uses a short-lived sync session.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._engine = None
        return cls._instance

    def _session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.config import settings

        url = _sync_url(settings.DATABASE_URL or "")
        if self._engine is None or str(self._engine.url) != url:
            self._engine = create_engine(url, future=True)
            self._Session = sessionmaker(self._engine, expire_on_commit=False)
        return self._Session()

    def add(
        self,
        user_id: str,
        name: str,
        base_url: str,
        api_key: str = "",
        api_format: str = "openai",
        default_model: str = "",
        headers: Optional[dict] = None,
    ) -> str:
        from core.database import CustomEndpointRecord, gen_id

        eid = gen_id()
        with self._session() as s:
            s.add(
                CustomEndpointRecord(
                    id=eid,
                    user_id=user_id,
                    name=name,
                    base_url=base_url,
                    api_key=api_key or None,
                    api_format=api_format or "openai",
                    default_model=default_model or None,
                    headers=headers or {},
                    enabled=True,
                )
            )
            s.commit()
        return eid

    def list_for_user(self, user_id: str) -> list[CustomEndpoint]:
        from core.database import CustomEndpointRecord
        from sqlalchemy import select

        with self._session() as s:
            rows = s.execute(
                select(CustomEndpointRecord)
                .where(CustomEndpointRecord.user_id == user_id)
                .order_by(CustomEndpointRecord.created_at.desc())
            ).scalars().all()
            return [
                CustomEndpoint(
                    id=r.id,
                    name=r.name,
                    base_url=r.base_url,
                    api_key=r.api_key or "",
                    api_format=r.api_format or "openai",
                    default_model=r.default_model or "",
                    headers=r.headers or {},
                    enabled=bool(r.enabled),
                    user_id=r.user_id,
                )
                for r in rows
            ]

    def get(self, endpoint_id: str) -> Optional[CustomEndpoint]:
        from core.database import CustomEndpointRecord

        with self._session() as s:
            r = s.get(CustomEndpointRecord, endpoint_id)
            if not r:
                return None
            return CustomEndpoint(
                id=r.id,
                name=r.name,
                base_url=r.base_url,
                api_key=r.api_key or "",
                api_format=r.api_format or "openai",
                default_model=r.default_model or "",
                headers=r.headers or {},
                enabled=bool(r.enabled),
                user_id=r.user_id,
            )

    def delete(self, endpoint_id: str, user_id: str) -> bool:
        from core.database import CustomEndpointRecord
        from sqlalchemy import delete

        with self._session() as s:
            res = s.execute(
                delete(CustomEndpointRecord).where(
                    CustomEndpointRecord.id == endpoint_id,
                    CustomEndpointRecord.user_id == user_id,
                )
            )
            s.commit()
            return (res.rowcount or 0) > 0

    def update(self, endpoint_id: str, user_id: str, **fields) -> bool:
        from core.database import CustomEndpointRecord

        allowed = {"name", "base_url", "api_key", "api_format", "default_model", "enabled"}
        with self._session() as s:
            r = s.get(CustomEndpointRecord, endpoint_id)
            if not r or r.user_id != user_id:
                return False
            changed = False
            for k, v in fields.items():
                if k in allowed:
                    setattr(r, k, v)
                    changed = True
            if changed:
                s.commit()
            return changed
