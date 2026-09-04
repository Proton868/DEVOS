"""Canonical DevOS identity contract — documentation-as-code.

AUTHENTICATED SUBJECT
 → CANONICAL ACCOUNT
 → RESOURCE OWNERSHIP / TENANT SCOPE
 → SPECIALTY POLICY
 → UCIP
 → EXECUTION

Frontend state (role/plan/user_id in localStorage) is never authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class DevOSIdentity:
    subject_id: str          # authenticated principal (JWT sub / supabase sub)
    account_id: str          # canonical DevOS users.id
    auth_provider: str       # local | supabase
    session_id: Optional[str] = None
    role: str = "member"     # member | elder | hegemon — label, not UCIP
    plan: str = "recruit"    # product entitlement label
    status: str = "active"
    workspace_scope: Optional[str] = None
    tenant_scope: Optional[str] = None
    authorization_context: dict = field(default_factory=dict)

    def owns_account(self, account_id: str) -> bool:
        return bool(account_id) and account_id == self.account_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "account_id": self.account_id,
            "auth_provider": self.auth_provider,
            "session_id": self.session_id,
            "role": self.role,
            "plan": self.plan,
            "status": self.status,
            "workspace_scope": self.workspace_scope,
            "tenant_scope": self.tenant_scope,
        }


def assert_account_ownership(identity: DevOSIdentity, resource_account_id: str) -> None:
    """Fail closed on cross-account access. Raise PermissionError (map to 404/403 in routes)."""
    if not identity.owns_account(resource_account_id):
        raise PermissionError("resource_not_owned")


def reject_client_authority_fields(payload: Optional[dict]) -> dict:
    """Strip fields a hostile client must never use to escalate."""
    if not payload:
        return {}
    blocked = {
        "role", "plan", "is_admin", "user_id", "account_id", "owner_id",
        "subject_id", "tenant_id", "workspace_id", "capabilities", "xp",
    }
    return {k: v for k, v in payload.items() if k not in blocked}
