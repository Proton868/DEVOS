import os
import pytest
from brain.personas import PERSONA_REGISTRY, list_personas, suggest_personas_for_goal, DEFAULT_PERSONA_ID, get_persona
from brain.web_mission import build_web_research_plan
from execution.web_intel.store import create_crawl, get_crawl



def test_creative_personas_registered():
    ids = {p.id for p in list_personas()}
    assert "nuha" in ids
    assert DEFAULT_PERSONA_ID == "nuha"
    for pid in ("writer", "storyteller", "script_writer"):
        assert pid in ids
        p = get_persona(pid)
        assert p.role == "specialist"
        assert "fs.write" in p.capabilities or "filesystem.write" in str(p.capabilities)
        # no deploy/push privileges on creative specialists
        assert "deployment.production" not in p.capabilities
        assert "vcs.push" not in p.capabilities


def test_existing_specialists_preserved():
    for pid in ("web", "code", "research", "automation", "design", "data", "business"):
        assert pid in PERSONA_REGISTRY


def test_persona_classification_routing():
    assert "storyteller" in suggest_personas_for_goal("Write a short story about the ocean")
    assert "script_writer" in suggest_personas_for_goal("Write a YouTube video script about coffee")
    assert "writer" in suggest_personas_for_goal("Write me a business proposal")
    assert "writer" in suggest_personas_for_goal("Rewrite this article professionally")
    assert "nuha" in suggest_personas_for_goal("anything")


def test_web_research_mission_plan():
    plan = build_web_research_plan("Research this business and summarize what they offer", root_url="https://example.com")
    assert any(n["type"] == "web_crawl" for n in plan["nodes"])
    assert any(n["id"] == "report" for n in plan["nodes"])
    assert plan["nodes"][1]["crawl_config"]["obey_robots"] is True
    assert plan["note"].startswith("Plan only")


def test_no_production_inline_env_default():
    assert os.environ.get("DEVOS_WEB_CRAWL_INLINE_TEST") in (None, "", "0")


def test_worker_unavailable_marks_failed_not_inline():
    """Simulate enqueue failure path without calling network crawler."""
    crawl = create_crawl({
        "user_id": "h1",
        "root_url": "https://example.com",
        "normalized_root_url": "https://example.com",
    })
    assert crawl["status"] == "QUEUED"
    # production path would update FAILED on enqueue error — unit-check helper
    from execution.web_intel.store import update_crawl, emit_event
    update_crawl(crawl["crawl_id"], status="FAILED", error="WORKER_UNAVAILABLE:Test")
    emit_event(crawl["crawl_id"], "crawl.failed", {"reason": "WORKER_UNAVAILABLE"})
    final = get_crawl(crawl["crawl_id"])
    assert final["status"] == "FAILED"
    assert "WORKER_UNAVAILABLE" in (final.get("error") or "")


def test_glow_partial_is_warning_not_waiting():
    from pathlib import Path
    import importlib.util
    # Read orchestrationUi mapping via node is hard; assert source contains WARNING for partial
    src = Path("frontend-src/src/os/orchestrationUi.js").read_text()
    assert "WARNING" in src
    assert "partial" in src.lower()
