"""Build Mission/DAG-oriented web research plans — does not execute the crawler."""
from __future__ import annotations

from typing import Any, Optional

from brain.delivery_intent import classify_delivery_intent, crawl_budgets_from_text


def build_web_research_plan(goal: str, *, root_url: Optional[str] = None) -> dict[str, Any]:
    intent = classify_delivery_intent(goal)
    budgets = crawl_budgets_from_text(goal)
    nodes = [
        {
            "id": "identify_target",
            "type": "inspect",
            "description": "Identify public research target / root URL",
            "persona_id": "nuha",
            "status": "pending",
        },
        {
            "id": "web_crawl",
            "type": "web_crawl",
            "description": "Crawl publicly available website pages",
            "persona_id": "research" if True else "nuha",
            "status": "pending",
            "capability": "web.intelligence",
            "crawl_config": {
                "root_url": root_url,
                **budgets,
                "obey_robots": True,
                "same_domain_only": True,
            },
        },
        {
            "id": "extract_business",
            "type": "analyze",
            "description": "Extract public business information from crawl evidence",
            "persona_id": "research",
            "status": "pending",
        },
        {
            "id": "extract_social",
            "type": "analyze",
            "description": "Extract discovered public social links",
            "persona_id": "research",
            "status": "pending",
        },
        {
            "id": "report",
            "type": "verify",
            "description": "Produce research report from evidence only",
            "persona_id": "writer",
            "status": "pending",
        },
    ]
    edges = [
        {"from": "identify_target", "to": "web_crawl"},
        {"from": "web_crawl", "to": "extract_business"},
        {"from": "web_crawl", "to": "extract_social"},
        {"from": "extract_business", "to": "report"},
        {"from": "extract_social", "to": "report"},
    ]
    return {
        "goal": goal,
        "intent": intent.to_dict() if intent else None,
        "nodes": nodes,
        "edges": edges,
        "requires_hitl": False,
        "risk_level": "low",
        "note": "Plan only — execution requires Mission Engine + UCIP + Jobs worker",
    }
