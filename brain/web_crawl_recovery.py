"""Map web crawl failures into existing NuhaDecision vocabulary.

Does NOT implement recovery itself — only diagnoses for Mission/Nuha path.
"""
from __future__ import annotations

from typing import Any, Optional


def diagnose_crawl_outcome(crawl: dict) -> dict[str, Any]:
    """Return structured diagnosis for ExecutionEvidence / NuhaDecision."""
    st = (crawl or {}).get("status") or "FAILED"
    err = (crawl or {}).get("error") or ""
    err_l = err.lower()

    decision = "COMPLETE"
    failure_class = None
    reason = st

    if st == "COMPLETED":
        decision = "COMPLETE"
    elif st == "PARTIAL":
        decision = "COMPLETE_PARTIAL"
        failure_class = "BUDGET_OR_LIMIT"
        reason = err or "partial_crawl"
    elif st == "CANCELLED":
        decision = "ABORT"
        failure_class = "CANCELLED"
    elif st == "FAILED":
        if "worker_unavailable" in err_l:
            decision = "RETRY"
            failure_class = "WORKER"
        elif "timeout" in err_l or "timed out" in err_l:
            decision = "RETRY"
            failure_class = "TIMEOUT"
        elif "blocked" in err_l or "ssrf" in err_l or "private" in err_l:
            decision = "ABORT"
            failure_class = "NETWORK_POLICY"
        elif "robots" in err_l or "disallow" in err_l:
            decision = "REPLAN"
            failure_class = "ROBOTS_DENIED"
        elif "403" in err_l or "401" in err_l or "auth" in err_l:
            decision = "ASK_USER"
            failure_class = "AUTH_REQUIRED"
        else:
            decision = "RETRY"
            failure_class = "FETCH_FAILURE"

    # NuhaDecision enum-compatible labels
    allowed = {"RETRY", "REPAIR", "REPLAN", "ASK_USER", "ABORT", "COMPLETE", "COMPLETE_PARTIAL"}
    if decision not in allowed:
        decision = "ABORT"

    return {
        "crawl_id": crawl.get("crawl_id"),
        "crawl_status": st,
        "decision": decision,
        "failure_class": failure_class,
        "reason": reason,
        "stats": crawl.get("stats_json"),
        "note": "Diagnosis only — Mission Engine must re-authorize any RETRY/REPLAN node via UCIP",
    }
