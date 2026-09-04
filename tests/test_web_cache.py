import time
import uuid
from execution.web_intel import cache as web_cache
from execution.web_intel.crawler import _safe_fetch
from brain.web_mission_exec import materialize_web_crawl_node, apply_crawl_result_to_node
from execution.web_intel.store import get_crawl


def _u(suffix=""):
    return f"https://example.com/devos-cache/{uuid.uuid4().hex}{suffix}"


def test_cache_miss_fresh_stale():
    url = _u("/a")
    assert web_cache.lookup(url)["status"] == "MISSING"
    body = b"<html><title>A</title>hello world content here</html>"
    web_cache.put(url, body=body, content_type="text/html", http_status=200, etag='"v1"', ttl_seconds=3600)
    hit = web_cache.lookup(url)
    assert hit["status"] == "FRESH"
    assert hit["entry"]["content_hash"] is not None
    import sqlite3, time as _t
    from execution.web_intel.cache import _DB, cache_key_for
    key = cache_key_for(url)
    c = sqlite3.connect(str(_DB))
    c.execute("UPDATE web_cache SET expires_at=? WHERE cache_key=?", (_t.time() - 10, key))
    c.commit(); c.close()
    assert web_cache.lookup(url)["status"] == "STALE"


def test_same_hash_preserves_fetched_at():
    url = _u("/b")
    body = b"same-body-content-xyz"
    web_cache.put(url, body=body, content_type="text/plain", http_status=200, ttl_seconds=100)
    e1 = web_cache.lookup(url)["entry"]
    t0 = e1["fetched_at"]
    time.sleep(0.05)
    web_cache.put(url, body=body, content_type="text/plain", http_status=200, ttl_seconds=100)
    e2 = web_cache.lookup(url)["entry"]
    assert e2["fetched_at"] == t0
    assert e2["version"] == 1


def test_content_change_bumps_version():
    url = _u("/c")
    web_cache.put(url, body=b"v1-content", content_type="text/plain", http_status=200, ttl_seconds=100)
    web_cache.put(url, body=b"v2-content-changed", content_type="text/plain", http_status=200, ttl_seconds=100)
    e = web_cache.lookup(url)["entry"]
    assert e["version"] >= 2
    assert e["previous_hash"] is not None


def test_provenance_fields():
    url = _u("/d")
    web_cache.put(url, body=b"abc", content_type="text/plain", http_status=200, ttl_seconds=100)
    e = web_cache.lookup(url)["entry"]
    p = web_cache.provenance(e, cache_status="CACHE_HIT")
    assert p["cached"] is True
    assert p["fetched_at"] is not None
    assert p["cache_used_at"] is not None


def test_cache_hit_via_safe_fetch_no_network_for_fresh():
    url2 = _u("/hit")
    body = b"<html><title>Cached</title>ok</html>"
    web_cache.put(url2, body=body, content_type="text/html", http_status=200, ttl_seconds=9999)
    b, st, ct, final, prov = _safe_fetch(url2, timeout=2.0, max_bytes=10000)
    assert b == body
    assert prov["cache_status"] == "CACHE_HIT"
    assert prov["cached"] is True


def test_mission_node_materialize():
    node = materialize_web_crawl_node(
        user_id="u1",
        goal="Research this business",
        root_url="https://example.com",
        mission_id="m1",
    )
    assert node["type"] == "web_crawl"
    assert node["crawl_id"]
    assert node["job_type"] == "web_crawl"
    assert get_crawl(node["crawl_id"])["status"] == "QUEUED"
    mapped = apply_crawl_result_to_node(node, {"crawl_id": node["crawl_id"], "status": "PARTIAL", "error": "PAGE_BUDGET"})
    assert mapped["status"] == "PARTIAL"


def test_ssrf_still_blocks_uncached_private():
    b, st, ct, err, prov = _safe_fetch("http://127.0.0.1/", timeout=1.0, max_bytes=1000)
    assert b is None
    assert "blocked" in (err or "").lower() or prov.get("cache_status") == "BLOCKED"
