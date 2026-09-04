import time
from execution.web_intel.store import create_crawl, get_crawl, list_pages, upsert_page
from execution.web_intel.crawler import run_crawl, _text_similarity, _normalize_text_for_sim, _MAX_CRAWL_DELAY
from execution.web_intel.job_handler import handle_web_crawl_job
from brain.delivery_intent import classify_delivery_intent, crawl_budgets_from_text


class FakeJob:
    def __init__(self, crawl_id):
        self.payload = {"crawl_id": crawl_id}
        self.id = "job-test"


def test_async_shape_create_queued_not_complete():
    c = create_crawl({
        "user_id": "w1",
        "root_url": "http://10.0.0.2/",
        "normalized_root_url": "http://10.0.0.2/",
        "max_pages": 1,
        "obey_robots": 0,
        "sitemap_enabled": 0,
    })
    assert c["status"] == "QUEUED"
    # job handler runs crawl
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(handle_web_crawl_job(FakeJob(c["crawl_id"])))
    assert result["crawl_id"] == c["crawl_id"]
    final = get_crawl(c["crawl_id"])
    assert final["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED")


def test_stale_fetching_recovery():
    c = create_crawl({
        "user_id": "w2",
        "root_url": "http://10.0.0.3/",
        "normalized_root_url": "http://10.0.0.3/",
        "max_pages": 1,
        "obey_robots": 0,
        "sitemap_enabled": 0,
    })
    upsert_page({
        "crawl_id": c["crawl_id"], "url": "http://10.0.0.3/",
        "normalized_url": "http://10.0.0.3/", "depth": 0, "status": "FETCHING",
    })
    import asyncio
    asyncio.get_event_loop().run_until_complete(handle_web_crawl_job(FakeJob(c["crawl_id"])))
    pages = list_pages(c["crawl_id"])
    assert not any(p["status"] == "FETCHING" for p in pages)


def test_near_duplicate_similarity():
    a = "Welcome to our site. We offer nails and hair services in town."
    b = "Welcome to our site. We offer nails and hair services in town."
    assert _text_similarity(a, b) >= 0.92
    assert _text_similarity(a, "Completely different product catalog for software APIs") < 0.5


def test_crawl_delay_cap():
    assert _MAX_CRAWL_DELAY <= 30.0


def test_web_intent_and_budgets():
    i = classify_delivery_intent("Research this business and find everything publicly available")
    assert i is not None
    assert i.intent == "web_crawl"
    assert i.capability == "web.intelligence"
    b = crawl_budgets_from_text("Quickly check the site")
    assert b["max_pages"] <= 10
    b2 = crawl_budgets_from_text("Research the entire website")
    assert b2["max_pages"] >= 50
