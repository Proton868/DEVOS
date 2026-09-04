"""Delivery DAG helpers for Mission Engine — node specs only; orchestration stays in existing engine."""
from __future__ import annotations
from typing import Any


DELIVERY_NODE_TYPES = (
    "inspect", "build", "verify", "preview", "share", "publish",
    "git_commit", "github_push", "github_pr", "deploy", "deploy_verify",
)


def build_delivery_dag(goal: str = "preview") -> dict[str, Any]:
    """Return a dependency graph for a delivery mission. Mission Engine consumes this."""
    g = (goal or "preview").lower()
    nodes = [
        {"id": "inspect", "type": "inspect", "depends_on": []},
        {"id": "build", "type": "build", "depends_on": ["inspect"]},
        {"id": "verify", "type": "verify", "depends_on": ["build"]},
        {"id": "preview", "type": "preview", "depends_on": ["verify"]},
    ]
    if any(k in g for k in ("github", "push", "commit", "pr")):
        nodes += [
            {"id": "git_commit", "type": "git_commit", "depends_on": ["verify"]},
            {"id": "github_push", "type": "github_push", "depends_on": ["git_commit"], "side_effect": "EXTERNAL_SIDE_EFFECT"},
        ]
    if "pr" in g:
        nodes.append({"id": "github_pr", "type": "github_pr", "depends_on": ["github_push"], "side_effect": "EXTERNAL_SIDE_EFFECT"})
    if any(k in g for k in ("deploy", "vercel", "netlify")):
        dep = "github_push" if any(n["id"] == "github_push" for n in nodes) else "verify"
        nodes += [
            {"id": "deploy", "type": "deploy", "depends_on": [dep], "side_effect": "EXTERNAL_SIDE_EFFECT"},
            {"id": "deploy_verify", "type": "deploy_verify", "depends_on": ["deploy"]},
        ]
    if "publish" in g or "public" in g:
        nodes.append({"id": "publish", "type": "publish", "depends_on": ["preview"], "side_effect": "EXTERNAL_SIDE_EFFECT"})
    return {"goal": goal, "nodes": nodes, "engine": "mission_engine"}
