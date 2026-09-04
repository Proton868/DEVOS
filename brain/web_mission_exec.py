"""Execute a web_crawl Mission node via existing Jobs — no parallel runner.

Flow: node plan → UCIP (caller) → create_crawl → enqueue web_crawl job.
"""
from __future__ import annotations

from typing import Any, Optional

from brain.web_mission import build_web_research_plan
from brain.delivery_intent import crawl_budgets_from_text


def materialize_web_crawl_node(
    *,
    user_id: str,
    goal: str,
    root_url: str,
    project_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    persona_id: str = "research",
    force_refresh: bool = False,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create durable crawl + return job enqueue payload (caller must UCIP + enqueue)."""
    plan = build_web_research_plan(goal, root_url=root_url)
    budgets = crawl_budgets_from_text(goal)
    from execution.web_intel.store import create_crawl
    from execution.web_intel.url_norm import normalize_url

    nu = normalize_url(root_url)
    crawl = create_crawl({
        "user_id": user_id,
        "project_id": project_id,
        "persona_id": persona_id,
        "mission_id": mission_id,
        "root_url": root_url,
        "normalized_root_url": nu,
        "trace_id": trace_id,
        **budgets,
    })
    node = {
        "id": "web_crawl",
        "type": "web_crawl",
        "status": "QUEUED",
        "crawl_id": crawl["crawl_id"],
        "job_type": "web_crawl",
        "job_payload": {"crawl_id": crawl["crawl_id"], "force_refresh": force_refresh},
        "project_id": project_id,
        "mission_id": mission_id,
        "plan": plan,
    }
    return node


def apply_crawl_result_to_node(node: dict, crawl: dict) -> dict:
    """Map crawl terminal status → mission node status."""
    st = (crawl or {}).get("status") or ""
    mapping = {
        "COMPLETED": "COMPLETED",
        "PARTIAL": "PARTIAL",
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
        "CRAWLING": "RUNNING",
        "DISCOVERING": "RUNNING",
        "QUEUED": "QUEUED",
    }
    out = dict(node)
    out["status"] = mapping.get(st, st or "FAILED")
    out["crawl_id"] = crawl.get("crawl_id")
    out["crawl_status"] = st
    out["stats"] = crawl.get("stats_json")
    out["error"] = crawl.get("error")
    return out
