"""Web intelligence crawl store — Postgres SoT."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional


def init_store() -> None:
    return


def new_id() -> str:
    return str(uuid.uuid4())


def create_crawl(data: dict) -> dict:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord

    cid = data.get("crawl_id") or new_id()
    uid = data.get("user_id") or ""
    with get_sync_session() as s:
        s.add(
            WebCrawlRecord(
                crawl_id=cid,
                user_id=uid,
                root_url=data.get("root_url"),
                normalized_root_url=data.get("normalized_root_url"),
                status=data.get("status") or "QUEUED",
                meta={k: v for k, v in data.items() if k not in ("crawl_id", "user_id", "root_url", "normalized_root_url", "status")},
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        s.commit()
    return {"crawl_id": cid, "user_id": uid}


def get_crawl(crawl_id: str) -> Optional[dict]:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord

    with get_sync_session() as s:
        r = s.get(WebCrawlRecord, crawl_id)
        if not r:
            return None
        return {
            "crawl_id": r.crawl_id,
            "user_id": r.user_id,
            "root_url": r.root_url,
            "normalized_root_url": r.normalized_root_url,
            "status": r.status,
            **(r.meta or {}),
        }


def list_crawls(user_id: str) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord
    from sqlalchemy import select

    with get_sync_session() as s:
        rows = s.execute(
            select(WebCrawlRecord).where(WebCrawlRecord.user_id == user_id)
        ).scalars().all()
        return [
            {
                "crawl_id": r.crawl_id,
                "user_id": r.user_id,
                "root_url": r.root_url,
                "status": r.status,
            }
            for r in rows
        ]


def update_crawl(crawl_id: str, **fields) -> bool:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord

    with get_sync_session() as s:
        r = s.get(WebCrawlRecord, crawl_id)
        if not r:
            return False
        if "status" in fields:
            r.status = fields["status"]
        meta = dict(r.meta or {})
        for k, v in fields.items():
            if k not in ("status", "user_id"):
                meta[k] = v
        r.meta = meta
        r.updated_at = time.time()
        s.commit()
        return True


def emit_event(crawl_id: str, event: dict) -> None:
    return


def list_events(crawl_id: str, limit: int = 50) -> list:
    return []


def upsert_page(**fields) -> str:
    return new_id()


def get_page(page_id: str) -> Optional[dict]:
    return None


def list_pages(crawl_id: str) -> list:
    return []


def claim_queued_pages(limit: int = 10) -> list:
    return []
