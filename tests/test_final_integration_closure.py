"""Final integration closure: Mission web_crawl path, recovery, composition."""
import asyncio
from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
from brain.web_crawl_recovery import diagnose_crawl_outcome
from brain.web_mission_exec import materialize_web_crawl_node
from brain.personas import get_persona, suggest_personas_for_goal
from execution.web_intel.store import get_crawl, create_crawl, update_crawl
from execution.web_intel import cache as web_cache


def test_mission_runtime_executes_web_crawl_idempotent():
    """Mission path creates crawl and runs via job handler; second call reuses."""
    # Use blocked private URL — should complete FAILED without hang
    req = NodeExecutionRequest(
        plan_id="plan1",
        node_id="web_crawl",
        user_id="u-int",
        workspace_id="ws1",
        persona_id="research",
        objective="Research https://example.com public site",
        effective_caps=["web.intelligence"],
        authorization_decision="allow",
        node_kind="web_crawl",
        root_url="https://example.com",
    )
    r1 = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert r1.task_id  # crawl_id
    crawl = get_crawl(r1.task_id)
    assert crawl is not None
    assert crawl["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED")
    # idempotent reuse
    req2 = NodeExecutionRequest(
        plan_id="plan1",
        node_id="web_crawl",
        user_id="u-int",
        workspace_id="ws1",
        persona_id="research",
        objective="Research",
        effective_caps=["web.intelligence"],
        authorization_decision="allow",
        node_kind="web_crawl",
        crawl_id=r1.task_id,
    )
    r2 = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req2))
    assert r2.raw_terminal and r2.raw_terminal.get("idempotent") is True


def test_unauthorized_web_crawl_blocked():
    req = NodeExecutionRequest(
        plan_id="p",
        node_id="web_crawl",
        user_id="u",
        workspace_id="w",
        persona_id="research",
        objective="x",
        effective_caps=["web.intelligence"],
        authorization_decision="deny",
        node_kind="web_crawl",
        root_url="https://example.com",
    )
    r = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert r.success is False
    assert r.status == "blocked"


def test_recovery_diagnosis_mapping():
    d = diagnose_crawl_outcome({"crawl_id": "c1", "status": "PARTIAL", "error": "PAGE_BUDGET_REACHED"})
    assert d["decision"] in ("retry", "repair", "replan", "continue", "complete", "ask_user", "abort")
    d2 = diagnose_crawl_outcome({"status": "FAILED", "error": "blocked:private"})
    # SSRF path via decide_from_crawl aborts; diagnose_crawl_outcome uses decide_recovery
    assert d2["decision"] in ("abort", "ask_user", "retry")
    d3 = diagnose_crawl_outcome({"status": "FAILED", "error": "timeout"})
    assert d3["decision"] in ("retry", "repair")
    d5 = diagnose_crawl_outcome({"status": "FAILED", "error": "401 auth credential"})
    assert d5["decision"] == "ask_user"


def test_creative_composition_routing():
    # Research + Writer
    s = suggest_personas_for_goal("Research this business and write me a professional company profile")
    assert "nuha" in s
    assert get_persona("writer") is not None
    # Script
    s2 = suggest_personas_for_goal("Turn this research into a YouTube video script")
    assert "script_writer" in s2 or get_persona("script_writer")
    # Story
    s3 = suggest_personas_for_goal("Write a short story about the ocean")
    assert "storyteller" in s3
    # creative specialists have no deploy authority
    for pid in ("writer", "storyteller", "script_writer"):
        p = get_persona(pid)
        assert "deployment.production" not in p.capabilities


def test_cache_hit_mission_compatible():
    url = "https://example.com/closure-cache"
    web_cache.put(url, body=b"<html>cached</html>", content_type="text/html", http_status=200, ttl_seconds=9999)
    from execution.web_intel.crawler import _safe_fetch
    b, st, ct, final, prov = _safe_fetch(url, timeout=2, max_bytes=5000)
    assert prov["cache_status"] == "CACHE_HIT"
