"""Web intelligence crawl store — Postgres SoT (pages + events durable)."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional


def init_store() -> None:
    return


def new_id() -> str:
    return str(uuid.uuid4())


def _page_to_dict(r) -> dict:
    meta = dict(r.meta or {})
    out = {
        "page_id": r.page_id,
        "id": r.page_id,
        "crawl_id": r.crawl_id,
        "url": r.url,
        "normalized_url": r.normalized_url,
        "depth": r.depth,
        "status": r.status,
        "parent_page_id": r.parent_page_id,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }
    out.update(meta)
    return out


def _crawl_to_dict(r) -> dict:
    meta = dict(r.meta or {})
    out = {
        "crawl_id": r.crawl_id,
        "user_id": r.user_id,
        "root_url": r.root_url,
        "normalized_root_url": r.normalized_root_url,
        "status": r.status,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }
    out.update(meta)
    return out


def create_crawl(data: dict) -> dict:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord

    cid = data.get("crawl_id") or new_id()
    uid = data.get("user_id") or ""
    status = data.get("status") or "QUEUED"
    reserved = {
        "crawl_id", "user_id", "root_url", "normalized_root_url", "status",
        "created_at", "updated_at",
    }
    meta = {k: v for k, v in data.items() if k not in reserved}
    now = time.time()
    with get_sync_session() as s:
        existing = s.get(WebCrawlRecord, cid)
        if existing is None:
            s.add(
                WebCrawlRecord(
                    crawl_id=cid,
                    user_id=uid,
                    root_url=data.get("root_url"),
                    normalized_root_url=data.get("normalized_root_url"),
                    status=status,
                    meta=meta,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.user_id = uid or existing.user_id
            existing.root_url = data.get("root_url", existing.root_url)
            existing.normalized_root_url = data.get(
                "normalized_root_url", existing.normalized_root_url
            )
            existing.status = status
            existing.meta = {**(existing.meta or {}), **meta}
            existing.updated_at = now
        s.commit()
    return get_crawl(cid) or {
        "crawl_id": cid,
        "user_id": uid,
        "status": status,
        "root_url": data.get("root_url"),
        "normalized_root_url": data.get("normalized_root_url"),
        **meta,
    }


def get_crawl(crawl_id: str) -> Optional[dict]:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord

    with get_sync_session() as s:
        r = s.get(WebCrawlRecord, crawl_id)
        if not r:
            return None
        return _crawl_to_dict(r)


def list_crawls(user_id: str) -> list[dict]:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord
    from sqlalchemy import select

    with get_sync_session() as s:
        rows = s.execute(
            select(WebCrawlRecord).where(WebCrawlRecord.user_id == user_id)
        ).scalars().all()
        return [_crawl_to_dict(r) for r in rows]


def update_crawl(crawl_id: str, **fields) -> bool:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlRecord

    with get_sync_session() as s:
        r = s.get(WebCrawlRecord, crawl_id)
        if not r:
            return False
        if "status" in fields:
            r.status = fields["status"]
        if "root_url" in fields:
            r.root_url = fields["root_url"]
        if "normalized_root_url" in fields:
            r.normalized_root_url = fields["normalized_root_url"]
        if "user_id" in fields and fields["user_id"]:
            r.user_id = fields["user_id"]
        meta = dict(r.meta or {})
        for k, v in fields.items():
            if k not in (
                "status", "user_id", "root_url", "normalized_root_url",
                "crawl_id", "created_at", "updated_at",
            ):
                meta[k] = v
        r.meta = meta
        r.updated_at = time.time()
        s.commit()
        return True


def emit_event(
    crawl_id: str,
    event_or_type,
    payload: Optional[dict] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Persist crawl lifecycle events."""
    from core.sync_session import get_sync_session
    from core.database import WebCrawlEvent

    if isinstance(event_or_type, dict):
        event = dict(event_or_type)
        et = str(event.get("type") or event.get("event_type") or "event")
        body = {k: v for k, v in event.items() if k not in ("type", "event_type", "crawl_id")}
    else:
        et = str(event_or_type)
        body = dict(payload or {})
    if trace_id is not None:
        body.setdefault("trace_id", trace_id)
    eid = new_id()
    with get_sync_session() as s:
        s.add(
            WebCrawlEvent(
                id=eid,
                crawl_id=crawl_id,
                event_type=et,
                payload=body,
                created_at=time.time(),
            )
        )
        s.commit()


def list_events(crawl_id: str, limit: int = 50) -> list:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlEvent
    from sqlalchemy import select, desc

    with get_sync_session() as s:
        rows = s.execute(
            select(WebCrawlEvent)
            .where(WebCrawlEvent.crawl_id == crawl_id)
            .order_by(desc(WebCrawlEvent.created_at))
            .limit(limit)
        ).scalars().all()
        out = []
        for r in rows:
            item = {
                "id": r.id,
                "crawl_id": r.crawl_id,
                "type": r.event_type,
                "event_type": r.event_type,
                "created_at": r.created_at,
                **(r.payload or {}),
            }
            out.append(item)
        return out


def upsert_page(*args, **kwargs) -> str:
    """Accept a single mapping or keyword fields; durable upsert by page_id or crawl+url."""
    from core.sync_session import get_sync_session
    from core.database import WebCrawlPage
    from sqlalchemy import select

    if len(args) == 1 and isinstance(args[0], dict):
        fields = dict(args[0])
        fields.update(kwargs)
    elif args:
        raise TypeError("upsert_page expects a dict or keyword fields")
    else:
        fields = dict(kwargs)

    crawl_id = fields.get("crawl_id") or ""
    page_id = fields.get("page_id") or fields.get("id")
    normalized = fields.get("normalized_url")
    now = time.time()
    reserved = {
        "page_id", "id", "crawl_id", "url", "normalized_url", "depth",
        "status", "parent_page_id", "created_at", "updated_at",
    }
    meta = {k: v for k, v in fields.items() if k not in reserved}

    with get_sync_session() as s:
        row = None
        if page_id:
            row = s.get(WebCrawlPage, page_id)
        if row is None and crawl_id and normalized:
            row = s.execute(
                select(WebCrawlPage).where(
                    WebCrawlPage.crawl_id == crawl_id,
                    WebCrawlPage.normalized_url == normalized,
                )
            ).scalar_one_or_none()
        if row is None:
            page_id = page_id or new_id()
            s.add(
                WebCrawlPage(
                    page_id=page_id,
                    crawl_id=crawl_id,
                    url=fields.get("url"),
                    normalized_url=normalized,
                    depth=fields.get("depth"),
                    status=fields.get("status") or "QUEUED",
                    parent_page_id=fields.get("parent_page_id"),
                    meta=meta,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()
            return page_id

        if fields.get("url") is not None:
            row.url = fields.get("url")
        if normalized is not None:
            row.normalized_url = normalized
        if fields.get("depth") is not None:
            row.depth = fields.get("depth")
        if fields.get("status") is not None:
            row.status = fields.get("status")
        if fields.get("parent_page_id") is not None:
            row.parent_page_id = fields.get("parent_page_id")
        row.meta = {**(row.meta or {}), **meta}
        row.updated_at = now
        s.commit()
        return row.page_id


def get_page(page_id: str) -> Optional[dict]:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlPage

    with get_sync_session() as s:
        r = s.get(WebCrawlPage, page_id)
        if not r:
            return None
        return _page_to_dict(r)


def list_pages(crawl_id: str, status: Optional[str] = None) -> list:
    from core.sync_session import get_sync_session
    from core.database import WebCrawlPage
    from sqlalchemy import select

    with get_sync_session() as s:
        stmt = select(WebCrawlPage).where(WebCrawlPage.crawl_id == crawl_id)
        if status is not None:
            stmt = stmt.where(WebCrawlPage.status == status)
        rows = s.execute(stmt).scalars().all()
        return [_page_to_dict(r) for r in rows]


def claim_queued_pages(crawl_id: Optional[str] = None, limit: int = 10) -> list:
    """Atomically claim QUEUED pages → FETCHING for a crawl."""
    from core.sync_session import get_sync_session
    from core.database import WebCrawlPage
    from sqlalchemy import select

    now = time.time()
    with get_sync_session() as s:
        stmt = select(WebCrawlPage).where(WebCrawlPage.status == "QUEUED")
        if crawl_id:
            stmt = stmt.where(WebCrawlPage.crawl_id == crawl_id)
        stmt = stmt.limit(limit)
        rows = list(s.execute(stmt).scalars().all())
        out = []
        for r in rows:
            r.status = "FETCHING"
            r.updated_at = now
            out.append(_page_to_dict(r))
        s.commit()
        return out
