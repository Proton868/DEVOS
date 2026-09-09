"""Ownership checks for missions/approvals — pure helpers."""
from __future__ import annotations
from typing import Any, Optional


def assert_owner(resource_user_id: Optional[str], requester_id: str) -> bool:
    """Return True if requester owns resource. Never raises."""
    if not requester_id or not resource_user_id:
        return False
    return str(resource_user_id) == str(requester_id)


def deny_cross_user(plan: Any, requester_id: str) -> bool:
    """True if access must be denied."""
    owner = getattr(plan, "user_id", None)
    if owner is None and isinstance(plan, dict):
        owner = plan.get("user_id")
    return not assert_owner(owner, requester_id)
