"""Platform authorization hierarchy (server-side only).

Hegemon  — owner/creator-level authority (highest)
Elder    — highest administrative authority beneath Hegemon
Member   — normal authenticated user

Rules:
- Never authorize from client-supplied role/plan/is_admin.
- Never use raw_user_meta_data or localStorage as authority.
- is_admin alone is insufficient; role is the hierarchy source of truth.
- Legacy rows with is_admin=True and empty/unknown role map to Elder
  (not Hegemon) to avoid accidental owner elevation.
- UCIP / isolation / ownership checks remain independent and mandatory.
"""
from __future__ import annotations

from typing import Any, Optional

# Lower rank number = less authority
ROLE_RANK = {
    "member": 0,
    "elder": 1,
    "hegemon": 2,
}

CANONICAL_ROLES = frozenset(ROLE_RANK.keys())

# Display / import aliases → canonical
_ALIASES = {
    "owner": "hegemon",
    "creator": "hegemon",
    "admin": "elder",
    "administrator": "elder",
    "operator": "member",
    "user": "member",
    "recruit": "member",
}


def normalize_role(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    raw = _ALIASES.get(raw, raw)
    if raw in CANONICAL_ROLES:
        return raw
    return None


def effective_platform_role(user: Any) -> str:
    """Authoritative platform role from the authenticated User record."""
    if user is None:
        return "member"
    role = normalize_role(getattr(user, "role", None))
    if role:
        return role
    # Legacy: is_admin without role → Elder ceiling (never Hegemon)
    if bool(getattr(user, "is_admin", False)):
        return "elder"
    return "member"


def role_rank(user_or_role: Any) -> int:
    if isinstance(user_or_role, str):
        r = normalize_role(user_or_role) or "member"
        return ROLE_RANK[r]
    return ROLE_RANK[effective_platform_role(user_or_role)]


def is_hegemon(user: Any) -> bool:
    return effective_platform_role(user) == "hegemon"


def is_elder(user: Any) -> bool:
    return effective_platform_role(user) == "elder"


def is_member(user: Any) -> bool:
    return effective_platform_role(user) == "member"


def is_elder_or_above(user: Any) -> bool:
    return role_rank(user) >= ROLE_RANK["elder"]


def is_hegemon_or_above(user: Any) -> bool:
    return role_rank(user) >= ROLE_RANK["hegemon"]


def has_at_least(user: Any, minimum: str) -> bool:
    need = normalize_role(minimum) or "member"
    return role_rank(user) >= ROLE_RANK[need]


def require_at_least(user: Any, minimum: str, *, detail: Optional[str] = None) -> None:
    """Raise PermissionError if user lacks minimum platform role."""
    if not has_at_least(user, minimum):
        msg = detail or f"requires_role:{normalize_role(minimum) or minimum}"
        raise PermissionError(msg)


def can_administer_platform(user: Any) -> bool:
    """Elder or Hegemon — replaces bare is_admin for platform admin endpoints."""
    return is_elder_or_above(user)


def can_mutate_target_role(actor: Any, target: Any, new_role: Optional[str] = None) -> bool:
    """Whether actor may change target's platform role.

    - Members: never
    - Elder: may manage members only; cannot touch Hegemon or promote to Hegemon
    - Hegemon: may manage Elder/Member; cannot be demoted by non-Hegemon
    """
    if not is_elder_or_above(actor):
        return False
    actor_r = effective_platform_role(actor)
    target_r = effective_platform_role(target)
    desired = normalize_role(new_role) if new_role is not None else None

    if target_r == "hegemon" and actor_r != "hegemon":
        return False
    if desired == "hegemon" and actor_r != "hegemon":
        return False
    if actor_r == "elder":
        # Elder cannot manage other Elders' platform role or any Hegemon
        if target_r in ("elder", "hegemon"):
            return False
        if desired in ("elder", "hegemon"):
            return False
        return True
    # Hegemon
    return True


def sync_is_admin_flag(user: Any) -> bool:
    """Derive is_admin from role for backward-compatible JWT/UI fields.

    Hegemon and Elder are platform admins; Member is not.
    Does not write to DB — caller may assign user.is_admin = result.
    """
    return is_elder_or_above(user)


def public_role_label(user: Any) -> str:
    return effective_platform_role(user)
