"""Existing Jobs queue handler for web_crawl — not a new scheduler."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("devos.web_crawl_job")


async def handle_web_crawl_job(job) -> dict[str, Any]:
    """JobWorker handler. payload: {crawl_id: str}."""
    payload = job.payload if isinstance(job.payload, dict) else {}
    if isinstance(payload, str):
        import json
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    crawl_id = payload.get("crawl_id")
    if not crawl_id:
        raise ValueError("crawl_id required")
    from execution.web_intel.crawler import run_crawl, resume_crawl
    from execution.web_intel.store import get_crawl, list_pages, upsert_page

    crawl = get_crawl(crawl_id)
    if not crawl:
        raise ValueError("crawl_not_found")
    # recover stale FETCHING
    for p in list_pages(crawl_id, status="FETCHING"):
        upsert_page({**p, "status": "QUEUED"})
    if crawl.get("status") in ("PARTIAL", "FAILED", "CRAWLING", "DISCOVERING"):
        result = resume_crawl(crawl_id)
    else:
        result = run_crawl(crawl_id)
    return {
        "crawl_id": crawl_id,
        "status": result.get("status"),
        "stats": result.get("stats_json"),
    }
