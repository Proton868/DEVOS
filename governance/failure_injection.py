"""Deliberate failure injection for reliability drills.

Enable with DEVOS_FAIL_INJECT=point1,point2 or DEVOS_FAIL_INJECT=all
Never enable in production (checklist blocks it).
"""
from __future__ import annotations

import os
import logging

logger = logging.getLogger("devos.fail_inject")

_POINTS = {
    "after_job_create",
    "after_job_claim",
    "after_external_success",
    "after_result_record",
    "after_evidence",
    "after_trust_update",
    "before_heartbeat",
}


def _enabled() -> set[str]:
    raw = os.environ.get("DEVOS_FAIL_INJECT", "").strip()
    if not raw:
        return set()
    if raw.lower() == "all":
        return set(_POINTS)
    return {p.strip() for p in raw.split(",") if p.strip()}


class InjectedFailure(Exception):
    """Raised intentionally at a failure boundary for drills."""


def maybe_crash(point: str) -> None:
    if point in _enabled():
        logger.error("FAIL_INJECT crash at %s", point)
        raise InjectedFailure(f"injected failure at {point}")


def injection_active() -> bool:
    return bool(_enabled())
