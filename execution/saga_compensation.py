"""Formal, risk-aware Saga compensation policy.

Forward action ≠ compensation action.
Never invent destructive remote-history deletion.
UCIP remains the only authorization authority — policy only classifies risk.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Optional, Tuple


class CompensationMode(str, Enum):
    NONE = "none"
    AUTOMATIC = "automatic"
    CONDITIONAL = "conditional"
    MANUAL = "manual"


class CompensationRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CompensationOutcome(str, Enum):
    COMPENSATED = "compensated"
    SKIPPED = "skipped"
    NOT_FOUND = "not_found"
    DENIED = "denied"
    FAILED = "failed"
    MANUAL_REMEDIATION = "manual_remediation"
    ALREADY_COMPENSATED = "already_compensated"


@dataclass(frozen=True)
class CompensationPolicy:
    action: Optional[str]  # compensation action name, or None
    mode: CompensationMode
    risk_class: CompensationRisk
    requires_ucip: bool
    requires_hitl: bool
    conditions: Tuple[str, ...]
    reason: str

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "mode": self.mode.value,
            "risk_class": self.risk_class.value,
            "requires_ucip": self.requires_ucip,
            "requires_hitl": self.requires_hitl,
            "conditions": list(self.conditions),
            "reason": self.reason,
        }


# Explicit matrix: forward_action → policy
_POLICY: dict[str, CompensationPolicy] = {
    "inspect": CompensationPolicy(
        None, CompensationMode.NONE, CompensationRisk.LOW, False, False, (),
        "read-only inspection",
    ),
    "install": CompensationPolicy(
        None, CompensationMode.NONE, CompensationRisk.LOW, False, False, (),
        "workspace artifacts retained",
    ),
    "build": CompensationPolicy(
        None, CompensationMode.NONE, CompensationRisk.LOW, False, False, (),
        "build outputs retained",
    ),
    "verify": CompensationPolicy(
        None, CompensationMode.NONE, CompensationRisk.LOW, False, False, (),
        "read-only verification",
    ),
    "deploy_verify": CompensationPolicy(
        None, CompensationMode.NONE, CompensationRisk.LOW, False, False, (),
        "read-only verification",
    ),
    "preview": CompensationPolicy(
        "STOP_RUNTIME", CompensationMode.AUTOMATIC, CompensationRisk.LOW, False, False,
        ("resource.type==runtime", "resource.owned_by_saga"),
        "stop mission-owned ephemeral runtime",
    ),
    "start_runtime": CompensationPolicy(
        "STOP_RUNTIME", CompensationMode.AUTOMATIC, CompensationRisk.LOW, False, False,
        ("resource.type==runtime", "resource.owned_by_saga"),
        "stop mission-owned runtime",
    ),
    "create_share": CompensationPolicy(
        "REVOKE_SHARE", CompensationMode.AUTOMATIC, CompensationRisk.LOW, False, False,
        ("resource.type==share", "resource.owned_by_saga"),
        "revoke mission-owned share",
    ),
    "share": CompensationPolicy(
        "REVOKE_SHARE", CompensationMode.AUTOMATIC, CompensationRisk.LOW, False, False,
        ("resource.type==share", "resource.owned_by_saga"),
        "revoke mission-owned share",
    ),
    "create_preview": CompensationPolicy(
        "REVOKE_PREVIEW", CompensationMode.AUTOMATIC, CompensationRisk.LOW, False, False,
        ("resource.type==preview",),
        "revoke temporary preview credential",
    ),
    "create_tunnel": CompensationPolicy(
        "STOP_TUNNEL", CompensationMode.AUTOMATIC, CompensationRisk.MEDIUM, True, False,
        ("resource.type==tunnel", "resource.owned_by_saga"),
        "stop mission-owned cloudflare tunnel",
    ),
    "tunnel": CompensationPolicy(
        "STOP_TUNNEL", CompensationMode.AUTOMATIC, CompensationRisk.MEDIUM, True, False,
        ("resource.type==tunnel", "resource.owned_by_saga"),
        "stop mission-owned tunnel",
    ),
    "create_temp_artifact": CompensationPolicy(
        "DELETE_ARTIFACT", CompensationMode.CONDITIONAL, CompensationRisk.MEDIUM, True, False,
        ("resource.type==artifact", "resource.temporary", "resource.owned_by_saga"),
        "delete only temporary mission artifacts",
    ),
    "github_branch": CompensationPolicy(
        "DELETE_BRANCH", CompensationMode.CONDITIONAL, CompensationRisk.HIGH, True, True,
        ("resource.type==branch", "resource.owned_by_saga", "no_unrelated_commits", "not_protected"),
        "delete branch only if mission-owned and clean",
    ),
    "git_commit": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.HIGH, True, True, (),
        "preserve commit history",
    ),
    "github_commit": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.HIGH, True, True, (),
        "preserve commit",
    ),
    "github_push": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.CRITICAL, True, True, (),
        "do not rewrite remote history",
    ),
    "github_pr": CompensationPolicy(
        "CLOSE_PR", CompensationMode.CONDITIONAL, CompensationRisk.HIGH, True, True,
        ("resource.type==pull_request", "resource.owned_by_saga", "no_unrelated_activity"),
        "close PR only if mission-owned and inactive",
    ),
    "github_repo": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.CRITICAL, True, True, (),
        "never auto-delete repositories",
    ),
    "deploy": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.CRITICAL, True, True, (),
        "preserve provider deployment",
    ),
    "deploy_vercel": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.CRITICAL, True, True, (),
        "preserve Vercel deployment unless operator deactivates",
    ),
    "deploy_netlify": CompensationPolicy(
        None, CompensationMode.MANUAL, CompensationRisk.CRITICAL, True, True, (),
        "preserve Netlify deployment unless operator deactivates",
    ),
    "publish": CompensationPolicy(
        "REVOKE_PUBLICATION", CompensationMode.CONDITIONAL, CompensationRisk.HIGH, True, True,
        ("resource.type==publication", "resource.owned_by_saga"),
        "revoke publication only when explicitly mission-owned",
    ),
    "publication": CompensationPolicy(
        "REVOKE_PUBLICATION", CompensationMode.CONDITIONAL, CompensationRisk.HIGH, True, True,
        ("resource.type==publication", "resource.owned_by_saga"),
        "revoke publication only when explicitly mission-owned",
    ),
}


def policy_for(action: str) -> CompensationPolicy:
    key = (action or "").lower().strip()
    return _POLICY.get(
        key,
        CompensationPolicy(
            None, CompensationMode.MANUAL, CompensationRisk.HIGH, True, True, (),
            "unknown action — default manual remediation",
        ),
    )


def evaluate_conditions(policy: CompensationPolicy, resource: Optional[dict], context: Optional[dict] = None) -> tuple[bool, str]:
    """Return (allowed, reason). Does not authorize — only policy predicate."""
    context = context or {}
    resource = resource or {}
    if policy.mode == CompensationMode.NONE:
        return False, "none"
    if policy.mode == CompensationMode.MANUAL:
        return False, "manual_required"
    if policy.mode == CompensationMode.AUTOMATIC:
        # still require owned_by_saga when listed
        if "resource.owned_by_saga" in policy.conditions and not resource.get("owned_by_saga", True):
            return False, "not_owned_by_saga"
        if "resource.type==runtime" in policy.conditions and resource.get("type") not in (None, "runtime"):
            if resource.get("type") and resource.get("type") != "runtime":
                return False, "resource_type_mismatch"
        return True, "automatic"
    # CONDITIONAL
    for cond in policy.conditions:
        if cond == "resource.owned_by_saga" and not resource.get("owned_by_saga", False):
            return False, "not_owned_by_saga"
        if cond == "no_unrelated_commits" and resource.get("has_unrelated_commits"):
            return False, "unrelated_commits"
        if cond == "not_protected" and resource.get("protected"):
            return False, "protected_branch"
        if cond == "no_unrelated_activity" and resource.get("has_unrelated_activity"):
            return False, "unrelated_activity"
        if cond == "resource.temporary" and not resource.get("temporary"):
            return False, "not_temporary"
        if cond.startswith("resource.type=="):
            expected = cond.split("==", 1)[1]
            if resource.get("type") and resource.get("type") != expected:
                return False, "resource_type_mismatch"
    return True, "conditions_met"


def compensation_requires_hitl(policy: CompensationPolicy) -> bool:
    return policy.requires_hitl or policy.mode == CompensationMode.MANUAL or policy.risk_class in (
        CompensationRisk.HIGH, CompensationRisk.CRITICAL,
    ) and policy.mode != CompensationMode.AUTOMATIC
