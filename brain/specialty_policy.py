"""
Specialty UCIP policy definitions — declarative configuration only.

Evaluated conceptually through UCIP (identity + caps + HITL + always-blocked).
This is NOT a second authorization engine.

Effective authority is restrictive intersection across layers:
GLOBAL ∩ DEVOS ∩ WORKSPACE ∩ PERSONA ∩ TASK ∩ NODE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from governance.ucip import ALWAYS_BLOCKED_CAPS, HITL_REQUIRED_CAPS, TRUST_LEVEL_CAPS, TrustLevel

# Short names used in orchestration ↔ UCIP canonical names
CAP_ALIASES = {
    "fs.read": "ucip:filesystem.read",
    "fs.write": "ucip:filesystem.write",
    "fs.delete": "ucip:filesystem.delete",
    "shell.exec": "ucip:execution.bash",
    "web.search": "ucip:search.web",
    "workflow.write": "ucip:filesystem.write",
    "credentials.read": "ucip:secret.read",
    "production.delete": "ucip:filesystem.delete",
    "deployment.production": "ucip:network.outbound",
    "external.publish": "ucip:network.outbound",
    "db.drop": "ucip:filesystem.format",
}


def _canon(cap: str) -> str:
    try:
        from brain.capability_canon import canonicalize
        return canonicalize(cap)
    except Exception:
        c = (cap or "").strip()
        return CAP_ALIASES.get(c, c)


class ActionRisk(str):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    EXECUTION = "execution"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    IRREVERSIBLE = "irreversible"


# Map risk class → example capability families (informational + gating hints)
RISK_CAP_HINTS = {
    ActionRisk.READ_ONLY: {"fs.read", "web.search"},
    ActionRisk.REVERSIBLE_WRITE: {"fs.write"},
    ActionRisk.EXECUTION: {"shell.exec"},
    ActionRisk.EXTERNAL_SIDE_EFFECT: {"web.fetch", "email.send"},
    ActionRisk.IRREVERSIBLE: {"fs.delete", "production.delete", "db.drop"},
}


@dataclass
class SpecialtyPolicy:
    persona_id: str
    allow: set[str] = field(default_factory=set)
    deny: set[str] = field(default_factory=set)
    require_hitl: set[str] = field(default_factory=set)
    scope_paths: list[str] = field(default_factory=list)
    workspace: str = "current"
    trust_ceiling: TrustLevel = TrustLevel.OPERATOR
    allow_risk: set[str] = field(default_factory=lambda: {
        ActionRisk.READ_ONLY, ActionRisk.REVERSIBLE_WRITE, ActionRisk.EXECUTION,
    })
    deny_risk: set[str] = field(default_factory=lambda: {ActionRisk.IRREVERSIBLE})
    require_hitl_risk: set[str] = field(default_factory=lambda: {ActionRisk.EXTERNAL_SIDE_EFFECT})

    def to_dict(self) -> dict:
        return {
            "persona_id": self.persona_id,
            "allow": sorted(self.allow),
            "deny": sorted(self.deny),
            "require_hitl": sorted(self.require_hitl),
            "scope_paths": list(self.scope_paths),
            "workspace": self.workspace,
            "trust_ceiling": self.trust_ceiling.name,
            "allow_risk": sorted(self.allow_risk),
            "deny_risk": sorted(self.deny_risk),
            "require_hitl_risk": sorted(self.require_hitl_risk),
            "note": "Declarative specialty policy — UCIP evaluates; not a second auth engine.",
        }


# Specialty policies (least privilege profiles)
SPECIALTY_POLICIES: dict[str, SpecialtyPolicy] = {
    "nuha": SpecialtyPolicy(
        persona_id="nuha",
        allow={"fs.read", "fs.write", "shell.exec", "web.search", "workflow.write"},
        deny={"production.delete", "credentials.read"},
        require_hitl={"deployment.production", "external.publish"},
        scope_paths=["**"],
        trust_ceiling=TrustLevel.OPERATOR,
    ),
    "web": SpecialtyPolicy(
        persona_id="web",
        allow={"fs.read", "fs.write", "shell.exec"},
        deny={"credentials.read", "production.delete", "db.drop"},
        require_hitl={"deployment.production", "external.publish"},
        scope_paths=["src/**", "public/**", "*.html", "*.css", "*.js", "*.tsx", "*.jsx"],
        trust_ceiling=TrustLevel.OPERATOR,
    ),
    "code": SpecialtyPolicy(
        persona_id="code",
        allow={"fs.read", "fs.write", "shell.exec"},
        deny={"production.delete", "credentials.read"},
        require_hitl={"deployment.production"},
        scope_paths=["**"],
        trust_ceiling=TrustLevel.OPERATOR,
    ),
    "design": SpecialtyPolicy(
        persona_id="design",
        allow={"fs.read", "fs.write"},
        deny={"shell.exec", "credentials.read", "production.delete", "deployment.production"},
        require_hitl=set(),
        scope_paths=["src/**", "public/**", "design/**"],
        trust_ceiling=TrustLevel.ASSISTANT,
        allow_risk={ActionRisk.READ_ONLY, ActionRisk.REVERSIBLE_WRITE},
        deny_risk={ActionRisk.EXECUTION, ActionRisk.IRREVERSIBLE, ActionRisk.EXTERNAL_SIDE_EFFECT},
    ),
    "automation": SpecialtyPolicy(
        persona_id="automation",
        allow={"fs.read", "fs.write", "workflow.write"},
        deny={"production.delete", "credentials.read"},
        require_hitl={"deployment.production"},
        scope_paths=["**"],
        trust_ceiling=TrustLevel.OPERATOR,
    ),
    "research": SpecialtyPolicy(
        persona_id="research",
        allow={"web.search", "fs.read", "fs.write"},
        deny={"shell.exec", "production.delete", "deployment.production", "credentials.read"},
        require_hitl=set(),
        scope_paths=["research/**", "docs/**"],
        trust_ceiling=TrustLevel.ASSISTANT,
        allow_risk={ActionRisk.READ_ONLY, ActionRisk.REVERSIBLE_WRITE},
        deny_risk={ActionRisk.EXECUTION, ActionRisk.IRREVERSIBLE},
    ),
    "data": SpecialtyPolicy(
        persona_id="data",
        allow={"fs.read", "fs.write", "shell.exec"},
        deny={"production.delete", "credentials.read"},
        require_hitl={"db.drop"},
        scope_paths=["**"],
        trust_ceiling=TrustLevel.OPERATOR,
    ),
    "business": SpecialtyPolicy(
        persona_id="business",
        allow={"fs.read"},
        deny={"fs.write", "shell.exec", "production.delete", "credentials.read", "deployment.production"},
        require_hitl=set(),
        scope_paths=["docs/**"],
        trust_ceiling=TrustLevel.READ_ONLY,
        allow_risk={ActionRisk.READ_ONLY},
        deny_risk={
            ActionRisk.REVERSIBLE_WRITE, ActionRisk.EXECUTION,
            ActionRisk.EXTERNAL_SIDE_EFFECT, ActionRisk.IRREVERSIBLE,
        },
    ),
}


@dataclass
class PolicyDecision:
    allow: bool
    hitl_required: bool
    reasons: list[str] = field(default_factory=list)
    effective_caps: set[str] = field(default_factory=set)
    denied_caps: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "allow": self.allow,
            "hitl_required": self.hitl_required,
            "reasons": list(self.reasons),
            "effective_caps": sorted(self.effective_caps),
            "denied_caps": sorted(self.denied_caps),
        }


def get_specialty_policy(persona_id: str) -> SpecialtyPolicy:
    return SPECIALTY_POLICIES.get(
        (persona_id or "").lower(),
        SpecialtyPolicy(
            persona_id=persona_id or "unknown",
            allow={"fs.read"},
            deny={"production.delete", "credentials.read", "fs.delete"},
            trust_ceiling=TrustLevel.READ_ONLY,
        ),
    )


def evaluate_node_request(
    *,
    persona_id: str,
    requested_caps: set[str],
    risk: Optional[str] = None,
) -> PolicyDecision:
    """
    Restrictive intersection of specialty policy with global UCIP constants.
    Does not bypass UCIPGateway at tool time — narrows the request surface.
    """
    policy = get_specialty_policy(persona_id)
    reasons: list[str] = []
    denied: set[str] = set()
    hitl = False

    # Work in canonical UCIP names; keep original aliases for reporting
    requested = set(requested_caps or [])
    canon_map = {c: _canon(c) for c in requested}
    allow_canon = {_canon(c) for c in policy.allow}
    deny_canon = {_canon(c) for c in policy.deny} | set(ALWAYS_BLOCKED_CAPS)
    hitl_canon = {_canon(c) for c in policy.require_hitl} | set(HITL_REQUIRED_CAPS)

    remaining_orig: set[str] = set()
    for orig, can in canon_map.items():
        if can in deny_canon or orig in policy.deny:
            denied.add(orig)
            reasons.append(f"deny:{orig}")
            continue
        if allow_canon and can not in allow_canon and "*" not in allow_canon and orig not in policy.allow:
            denied.add(orig)
            reasons.append(f"not_in_specialty_allow:{orig}")
            continue
        try:
            from brain.capability_canon import to_ucip
            ucip_name = to_ucip(can)
        except Exception:
            ucip_name = can if can.startswith("ucip:") else f"ucip:{can}"
        tier = set(TRUST_LEVEL_CAPS.get(policy.trust_ceiling, set()))
        if "*" not in tier and ucip_name not in tier and can not in tier:
            denied.add(orig)
            reasons.append(f"above_trust_ceiling:{orig}")
            continue
        remaining_orig.add(orig)
        if can in hitl_canon or orig in policy.require_hitl:
            hitl = True
            reasons.append(f"hitl:{orig}")

    remaining = remaining_orig

    # Risk class
    if risk == ActionRisk.IRREVERSIBLE or risk == "critical":
        if ActionRisk.IRREVERSIBLE in policy.deny_risk or risk == "critical":
            if remaining & RISK_CAP_HINTS.get(ActionRisk.IRREVERSIBLE, set()) or risk == "critical":
                # irreversible requests need explicit allow which specialties deny by default
                if remaining & {"fs.delete", "production.delete", "db.drop"} or risk == "critical":
                    for cap in list(remaining):
                        if cap in {"fs.delete", "production.delete", "db.drop"}:
                            denied.add(cap)
                            remaining.discard(cap)
                            reasons.append(f"risk_irreversible:{cap}")
                    if risk == "critical" and not remaining:
                        reasons.append("critical_risk_denied_by_specialty")

    allow = len(remaining) > 0 and not (risk == "critical" and not remaining)
    # If all requested were denied, allow=False
    if requested_caps and not remaining:
        allow = False
        reasons.append("all_capabilities_denied")

    if hitl and allow:
        reasons.append("hitl_required_before_execution")

    return PolicyDecision(
        allow=allow,
        hitl_required=hitl,
        reasons=reasons,
        effective_caps=remaining,
        denied_caps=denied,
    )
