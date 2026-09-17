"""
Governed dynamic tool/skill acquisition for coding agents.

When an agent needs a capability that is not registered, it may *propose*
a skill definition. Installation only proceeds through this module:

  detect missing → propose → validate schema → security/governance checks
  → HITL if privileged → register capability + tool → isolation test
  → evidence/audit

Layers stay separate:
  skill definition  ≠  executable capability  ≠  authorization
  ≠  tool execution  ≠  evidence

Agents cannot silently grant themselves unrestricted permissions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("devos.skill_acquisition")

# ── Forbidden patterns in generated handlers / definitions ───────────────────

_FORBIDDEN_SLUG = re.compile(r"[^a-z0-9._:-]", re.I)
_DANGEROUS_NAME_BITS = (
    "sudo", "rm_rf", "format_disk", "raw_shell", "unrestricted",
    "bypass_ucip", "disable_auth", "exfiltrate", "drop_table",
)
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 2000
_MAX_SCHEMA_KEYS = 40


class SkillRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    INSTALLED = "installed"
    TEST_FAILED = "test_failed"


@dataclass
class SkillDefinition:
    """Declarative skill/tool proposal (not yet executable)."""

    name: str
    description: str
    capability_slug: str
    input_schema: dict = field(default_factory=dict)
    risk: SkillRisk = SkillRisk.MEDIUM
    side_effect: str = "none"  # none | workspace | network | system
    reason: str = ""  # why the agent needs this
    requested_by: str = ""  # agent / persona id
    tenant_id: str = "default"
    handler_kind: str = "echo"  # only safe built-in kinds allowed
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["risk"] = self.risk.value if isinstance(self.risk, SkillRisk) else str(self.risk)
        return d


@dataclass
class SkillProposal:
    proposal_id: str
    definition: SkillDefinition
    status: ProposalStatus = ProposalStatus.DRAFT
    validation_errors: list[str] = field(default_factory=list)
    security_notes: list[str] = field(default_factory=list)
    hitl_request_id: Optional[str] = None
    installed_tool: Optional[str] = None
    installed_capability: Optional[str] = None
    isolation_test: Optional[dict] = None
    evidence_ids: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "definition": self.definition.to_dict(),
            "status": self.status.value,
            "validation_errors": list(self.validation_errors),
            "security_notes": list(self.security_notes),
            "hitl_request_id": self.hitl_request_id,
            "installed_tool": self.installed_tool,
            "installed_capability": self.installed_capability,
            "isolation_test": self.isolation_test,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# In-process store (tests + single-node); durable evidence is separate.
_PROPOSALS: dict[str, SkillProposal] = {}
_INSTALLED: dict[str, str] = {}  # tool_name → proposal_id
_DYNAMIC_HANDLERS: dict[str, str] = {}  # tool_name → safe handler_kind

# Safe handler kinds only — no arbitrary code execution from model output.
_SAFE_HANDLERS: dict[str, Callable[..., dict]] = {}


def _register_safe_handler(kind: str, fn: Callable[..., dict]) -> None:
    _SAFE_HANDLERS[kind] = fn


def _handler_echo(args: dict, **_ctx) -> dict:
    return {"ok": True, "echo": args, "handler": "echo"}


def _handler_json_validate(args: dict, **_ctx) -> dict:
    payload = args.get("payload")
    if payload is None:
        return {"ok": False, "error": "payload required"}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as e:
            return {"ok": False, "error": f"invalid json: {type(e).__name__}"}
    return {"ok": True, "valid": True, "type": type(payload).__name__}


def _handler_hash_text(args: dict, **_ctx) -> dict:
    text = str(args.get("text") or "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"ok": True, "sha256": digest, "length": len(text)}


_register_safe_handler("echo", _handler_echo)
_register_safe_handler("json_validate", _handler_json_validate)
_register_safe_handler("hash_text", _handler_hash_text)


def detect_missing_capability(name_or_slug: str) -> dict:
    """Return whether a tool/capability is missing and a short explanation."""
    name = (name_or_slug or "").strip()
    out = {
        "name": name,
        "missing": True,
        "as_tool": False,
        "as_capability": False,
        "reason": "",
    }
    if not name:
        out["reason"] = "empty capability name"
        return out

    try:
        from brain.agent_tools import get_agent_tool

        tool = get_agent_tool(name)
        if tool is not None:
            out["missing"] = False
            out["as_tool"] = True
            out["reason"] = f"tool '{name}' is already registered"
            return out
    except Exception:
        pass

    try:
        from governance.capability_registry import get_registry

        reg = get_registry()
        slug = name if name.startswith("ucip:") else f"ucip:skill.{name}"
        cap = reg.get(slug) or reg.get(name)
        if cap is not None:
            out["missing"] = False
            out["as_capability"] = True
            out["reason"] = f"capability '{cap.slug}' is registered"
            return out
    except Exception:
        pass

    if name in _INSTALLED:
        out["missing"] = False
        out["reason"] = f"dynamically installed via proposal {_INSTALLED[name]}"
        return out

    out["reason"] = (
        f"No registered agent tool or UCIP capability named '{name}'. "
        "A skill proposal is required before use."
    )
    return out


def validate_skill_definition(defn: SkillDefinition) -> list[str]:
    """Schema + policy validation. Returns list of errors (empty = ok)."""
    errors: list[str] = []
    name = (defn.name or "").strip()
    if not name or len(name) > _MAX_NAME_LEN:
        errors.append("name required and must be ≤ 64 chars")
    if not re.match(r"^[a-z][a-z0-9_]*$", name or ""):
        errors.append("name must be snake_case starting with a letter")
    for bit in _DANGEROUS_NAME_BITS:
        if bit in name.lower():
            errors.append(f"name contains forbidden token '{bit}'")

    desc = (defn.description or "").strip()
    if not desc:
        errors.append("description required")
    if len(desc) > _MAX_DESC_LEN:
        errors.append("description too long")

    slug = (defn.capability_slug or "").strip()
    if not slug.startswith("ucip:skill."):
        errors.append("capability_slug must start with 'ucip:skill.'")
    if _FORBIDDEN_SLUG.search(slug.replace("ucip:skill.", "")):
        errors.append("capability_slug has invalid characters")

    schema = defn.input_schema or {}
    if schema and not isinstance(schema, dict):
        errors.append("input_schema must be an object")
    elif isinstance(schema, dict):
        props = schema.get("properties") or {}
        if len(props) > _MAX_SCHEMA_KEYS:
            errors.append("input_schema has too many properties")

    risk = defn.risk if isinstance(defn.risk, SkillRisk) else SkillRisk(str(defn.risk))
    side = (defn.side_effect or "none").lower()
    if side not in ("none", "workspace", "network", "system"):
        errors.append("side_effect must be none|workspace|network|system")
    if side in ("network", "system") and risk in (SkillRisk.LOW, SkillRisk.MEDIUM):
        errors.append("network/system side_effect requires risk high or critical")

    kind = (defn.handler_kind or "").strip()
    if kind not in _SAFE_HANDLERS:
        errors.append(
            f"handler_kind '{kind}' is not an allowed safe handler "
            f"(allowed: {sorted(_SAFE_HANDLERS)})"
        )

    if not (defn.reason or "").strip():
        errors.append("reason required (why the agent needs this capability)")

    return errors


def security_governance_check(defn: SkillDefinition) -> tuple[bool, list[str]]:
    """
    Fail-closed security review. Returns (allowed_to_proceed, notes).
    Does not grant authority — only gates whether proposal may advance.
    """
    notes: list[str] = []
    risk = defn.risk if isinstance(defn.risk, SkillRisk) else SkillRisk(str(defn.risk))

    # Never allow unrestricted / privilege-escalation shaped proposals
    blob = json.dumps(defn.to_dict(), default=str).lower()
    for token in (
        "bypass",
        "unrestricted",
        "require_authority=false",
        "disable governance",
        "all capabilities",
        "sudo",
    ):
        if token in blob:
            notes.append(f"security reject: contains '{token}'")
            return False, notes

    if risk == SkillRisk.CRITICAL:
        notes.append("critical risk requires human approval before install")
    elif risk == SkillRisk.HIGH:
        notes.append("high risk requires human approval before install")
    else:
        notes.append("risk within auto-installable band after validation")

    if defn.side_effect in ("network", "system"):
        notes.append("side_effect elevates privilege surface — approval required")

    # handler must remain in allowlist
    if defn.handler_kind not in _SAFE_HANDLERS:
        notes.append("handler_kind not in safe allowlist")
        return False, notes

    return True, notes


def requires_hitl(defn: SkillDefinition) -> bool:
    risk = defn.risk if isinstance(defn.risk, SkillRisk) else SkillRisk(str(defn.risk))
    if risk in (SkillRisk.HIGH, SkillRisk.CRITICAL):
        return True
    if (defn.side_effect or "").lower() in ("network", "system"):
        return True
    return False


def _evidence(action: str, actor_id: str, status: str, metadata: dict) -> str:
    node_id = f"skillacq-{uuid.uuid4().hex[:12]}"
    try:
        from governance.evidence import EvidenceChain, EvidenceChainManager

        chain_id = f"skill-acq-{metadata.get('tenant_id') or metadata.get('user_id') or 'default'}"
        chain = EvidenceChainManager.load(chain_id)
        if chain is None:
            chain = EvidenceChain(
                chain_id=chain_id,
                goal="skill_acquisition",
                identity_context={
                    "user_id": str(metadata.get("user_id") or ""),
                    "actor_id": actor_id or "system",
                    "tenant_id": str(metadata.get("tenant_id") or ""),
                },
            )
        meta = {k: v for k, v in (metadata or {}).items() if k not in ("token", "password", "secret")}
        meta["evidence_id"] = node_id
        chain.add_node(
            action=action,
            actor_id=actor_id or "system",
            status=status,
            metadata=meta,
        )
        chain.save()
    except Exception as e:
        logger.debug("evidence write skipped: %s", type(e).__name__)
    try:
        from governance.audit import AuditLogger, AuditEventType

        AuditLogger().log(
            AuditEventType.SYSTEM if hasattr(AuditEventType, "SYSTEM") else list(AuditEventType)[0],
            actor_id=actor_id or "system",
            tenant_id=str(metadata.get("tenant_id") or "default"),
            action=action,
            outcome=status,
            details={k: v for k, v in metadata.items() if k not in ("token", "secret")},
        )
    except Exception:
        pass
    return node_id


def propose_skill(
    *,
    name: str,
    description: str,
    reason: str,
    requested_by: str,
    tenant_id: str = "default",
    input_schema: Optional[dict] = None,
    risk: str = "medium",
    side_effect: str = "none",
    handler_kind: str = "echo",
    capability_slug: Optional[str] = None,
) -> SkillProposal:
    """Create a draft proposal after missing-capability detection."""
    slug = capability_slug or f"ucip:skill.{name.strip()}"
    try:
        risk_e = SkillRisk(str(risk).lower())
    except Exception:
        risk_e = SkillRisk.MEDIUM

    defn = SkillDefinition(
        name=name.strip(),
        description=description.strip(),
        capability_slug=slug,
        input_schema=input_schema or {"type": "object", "properties": {}},
        risk=risk_e,
        side_effect=side_effect,
        reason=reason.strip(),
        requested_by=requested_by,
        tenant_id=tenant_id,
        handler_kind=handler_kind,
    )
    pid = f"sp_{uuid.uuid4().hex[:16]}"
    prop = SkillProposal(proposal_id=pid, definition=defn, status=ProposalStatus.DRAFT)
    eid = _evidence(
        "skill.propose",
        requested_by,
        "success",
        {"proposal_id": pid, "name": name, "tenant_id": tenant_id, "reason": reason[:500]},
    )
    prop.evidence_ids.append(eid)
    _PROPOSALS[pid] = prop
    return prop


def validate_proposal(proposal_id: str) -> SkillProposal:
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")
    errors = validate_skill_definition(prop.definition)
    ok, notes = security_governance_check(prop.definition)
    prop.validation_errors = errors
    prop.security_notes = notes
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    if errors or not ok:
        prop.status = ProposalStatus.REJECTED
        _evidence(
            "skill.validate",
            prop.definition.requested_by,
            "failed",
            {
                "proposal_id": proposal_id,
                "errors": errors,
                "notes": notes,
                "tenant_id": prop.definition.tenant_id,
            },
        )
        return prop
    prop.status = ProposalStatus.VALIDATED
    _evidence(
        "skill.validate",
        prop.definition.requested_by,
        "success",
        {"proposal_id": proposal_id, "tenant_id": prop.definition.tenant_id},
    )
    return prop


def request_approval(proposal_id: str, user_id: str = "system") -> SkillProposal:
    """Mark privileged proposals as pending HITL approval."""
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")
    if prop.status == ProposalStatus.REJECTED:
        return prop
    if prop.status == ProposalStatus.DRAFT:
        validate_proposal(proposal_id)
        prop = _PROPOSALS[proposal_id]
    if prop.status == ProposalStatus.REJECTED:
        return prop

    if not requires_hitl(prop.definition):
        prop.status = ProposalStatus.APPROVED
        prop.updated_at = datetime.now(timezone.utc).isoformat()
        return prop

    prop.status = ProposalStatus.PENDING_APPROVAL
    hitl_id = f"hitl-skill-{proposal_id}"
    prop.hitl_request_id = hitl_id
    try:
        from governance.hitl import HITLManager, HITLRequest

        mgr = HITLManager()
        if hasattr(mgr, "submit") or hasattr(mgr, "create_request"):
            # Best-effort integration with existing HITL manager shapes.
            req_fn = getattr(mgr, "create_request", None) or getattr(mgr, "submit", None)
            if callable(req_fn):
                try:
                    result = req_fn(
                        action=f"skill.install:{prop.definition.name}",
                        reason=prop.definition.reason,
                        user_id=user_id,
                        risk=prop.definition.risk.value,
                    )
                    if isinstance(result, str):
                        prop.hitl_request_id = result
                    elif hasattr(result, "request_id"):
                        prop.hitl_request_id = result.request_id
                except TypeError:
                    pass
    except Exception as e:
        logger.debug("HITL integration optional: %s", type(e).__name__)

    _evidence(
        "skill.approval_requested",
        prop.definition.requested_by,
        "pending",
        {
            "proposal_id": proposal_id,
            "hitl_request_id": prop.hitl_request_id,
            "tenant_id": prop.definition.tenant_id,
        },
    )
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    return prop


def resolve_approval(proposal_id: str, approved: bool, resolved_by: str = "operator") -> SkillProposal:
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")
    if prop.status not in (ProposalStatus.PENDING_APPROVAL, ProposalStatus.VALIDATED):
        return prop
    if approved:
        prop.status = ProposalStatus.APPROVED
        outcome = "approved"
    else:
        prop.status = ProposalStatus.REJECTED
        outcome = "denied"
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    _evidence(
        "skill.approval_resolved",
        resolved_by,
        outcome,
        {"proposal_id": proposal_id, "tenant_id": prop.definition.tenant_id},
    )
    return prop


def _isolation_test(defn: SkillDefinition) -> dict:
    """Run the safe handler once — proves callable without network/system side effects.

    Handler logical failures (e.g. invalid JSON input) still count as a successful
    isolation run if the function returns a structured dict without raising.
    """
    fn = _SAFE_HANDLERS.get(defn.handler_kind)
    if not fn:
        return {"ok": False, "error": "no handler"}
    try:
        sample = {}
        props = (defn.input_schema or {}).get("properties") or {}
        for k, spec in list(props.items())[:5]:
            typ = (spec or {}).get("type")
            if typ == "string":
                # Prefer valid JSON when field name suggests payload/data
                if k in ("payload", "json", "data") or defn.handler_kind == "json_validate":
                    sample[k] = "{}"
                else:
                    sample[k] = "test"
            elif typ == "integer":
                sample[k] = 0
            elif typ == "boolean":
                sample[k] = False
            elif typ == "object":
                sample[k] = {}
            else:
                sample[k] = "test"
        result = fn(sample)
        if not isinstance(result, dict):
            return {"ok": False, "error": "handler must return dict"}
        return {
            "ok": True,
            "handler_ok": bool(result.get("ok", True)),
            "result_keys": sorted(result.keys()),
            "handler": defn.handler_kind,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def install_skill(proposal_id: str, *, force_approved: bool = False) -> SkillProposal:
    """
    Register capability + agent tool only after validation and approval gates.
    Never installs rejected or unapproved high-risk skills.
    """
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")

    if prop.status == ProposalStatus.DRAFT:
        validate_proposal(proposal_id)
        prop = _PROPOSALS[proposal_id]
    if prop.status == ProposalStatus.REJECTED:
        return prop

    if requires_hitl(prop.definition):
        if prop.status == ProposalStatus.VALIDATED:
            request_approval(proposal_id)
            prop = _PROPOSALS[proposal_id]
        if prop.status == ProposalStatus.PENDING_APPROVAL and not force_approved:
            return prop
        if prop.status not in (ProposalStatus.APPROVED, ProposalStatus.INSTALLED):
            if not force_approved:
                prop.validation_errors.append("privileged skill requires approval before install")
                return prop

    if prop.status not in (
        ProposalStatus.VALIDATED,
        ProposalStatus.APPROVED,
        ProposalStatus.INSTALLED,
    ) and not force_approved:
        prop.validation_errors.append(f"cannot install from status={prop.status.value}")
        return prop

    # Isolation test before registration
    test = _isolation_test(prop.definition)
    prop.isolation_test = test
    if not test.get("ok"):
        prop.status = ProposalStatus.TEST_FAILED
        _evidence(
            "skill.isolation_test",
            prop.definition.requested_by,
            "failed",
            {"proposal_id": proposal_id, "test": test, "tenant_id": prop.definition.tenant_id},
        )
        return prop

    defn = prop.definition
    # Capability registry
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        risk_map = {
            SkillRisk.LOW: CapabilityRisk.LOW,
            SkillRisk.MEDIUM: CapabilityRisk.MEDIUM,
            SkillRisk.HIGH: CapabilityRisk.HIGH,
            SkillRisk.CRITICAL: CapabilityRisk.CRITICAL,
        }
        cat = CapabilityCategory.SYSTEM
        if defn.side_effect == "none":
            cat = CapabilityCategory.EXECUTION
        elif defn.side_effect == "workspace":
            cat = CapabilityCategory.FILESYSTEM
        elif defn.side_effect == "network":
            cat = CapabilityCategory.NETWORK

        desc = CapabilityDescriptor(
            slug=defn.capability_slug,
            name=defn.name,
            category=cat,
            description=defn.description,
            risk=risk_map.get(defn.risk, CapabilityRisk.MEDIUM),
            input_schema=defn.input_schema or {},
            requires_hitl=requires_hitl(defn),
            requires_network=defn.side_effect == "network",
            is_reversible=defn.side_effect == "none",
        )
        get_registry().register(desc)
        prop.installed_capability = defn.capability_slug
    except Exception as e:
        prop.validation_errors.append(f"capability register failed: {type(e).__name__}")
        prop.status = ProposalStatus.REJECTED
        return prop

    # Agent tool metadata + dynamic handler table. Execution still goes through
    # AgentRuntime._execute_tool → run_dynamic_skill_handler, then UCIP at call site.
    try:
        from brain.agent_tools import (
            AgentTool,
            register_agent_tool,
            AgentMode,
            MODE_TOOLS,
            SideEffect,
            ToolRisk,
        )

        kind = defn.handler_kind
        if kind not in _SAFE_HANDLERS:
            raise ValueError(f"unsafe handler_kind {kind}")

        se = SideEffect.NONE
        if defn.side_effect == "workspace":
            se = SideEffect.LOCAL
        elif defn.side_effect in ("network", "system"):
            se = SideEffect.UNKNOWN

        risk_map = {
            SkillRisk.LOW: ToolRisk.LOW,
            SkillRisk.MEDIUM: ToolRisk.MEDIUM,
            SkillRisk.HIGH: ToolRisk.HIGH,
            SkillRisk.CRITICAL: ToolRisk.CRITICAL,
        }
        tool = AgentTool(
            name=defn.name,
            description=defn.description,
            input_schema=defn.input_schema or {"type": "object", "properties": {}},
            capability=defn.capability_slug,
            side_effect=se,
            risk=risk_map.get(defn.risk, ToolRisk.MEDIUM),
            timeout_s=30,
            durable=False,
        )
        register_agent_tool(tool)
        # Dynamic handler table (name → safe kind)
        _DYNAMIC_HANDLERS[defn.name] = kind
        # Allow in coding modes without granting admin-only tools
        for mode in (AgentMode.AGENT, AgentMode.EDIT):
            if mode in MODE_TOOLS:
                MODE_TOOLS[mode].add(defn.name)
        prop.installed_tool = defn.name
        _INSTALLED[defn.name] = proposal_id
    except Exception as e:
        prop.validation_errors.append(f"tool register failed: {type(e).__name__}: {e}")
        prop.status = ProposalStatus.REJECTED
        return prop

    prop.status = ProposalStatus.INSTALLED
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    eid = _evidence(
        "skill.install",
        prop.definition.requested_by,
        "success",
        {
            "proposal_id": proposal_id,
            "tool": prop.installed_tool,
            "capability": prop.installed_capability,
            "tenant_id": prop.definition.tenant_id,
        },
    )
    prop.evidence_ids.append(eid)
    logger.info(
        "[skill_acquisition] installed tool=%s capability=%s proposal=%s",
        prop.installed_tool,
        prop.installed_capability,
        proposal_id,
    )
    return prop


def get_proposal(proposal_id: str) -> Optional[SkillProposal]:
    return _PROPOSALS.get(proposal_id)


def list_proposals(tenant_id: Optional[str] = None) -> list[dict]:
    rows = list(_PROPOSALS.values())
    if tenant_id:
        rows = [p for p in rows if p.definition.tenant_id == tenant_id]
    return [p.to_dict() for p in rows]


def reset_store_for_tests() -> None:
    """Test helper — clear in-memory proposals (does not wipe CapabilityRegistry)."""
    _PROPOSALS.clear()
    _INSTALLED.clear()
    _DYNAMIC_HANDLERS.clear()


def run_dynamic_skill_handler(name: str, args: dict):
    kind = _DYNAMIC_HANDLERS.get(name)
    if not kind:
        return None
    fn = _SAFE_HANDLERS.get(kind)
    if not fn:
        return {"ok": False, "error": "handler missing"}
    try:
        return fn(args if isinstance(args, dict) else {})
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}



def acquire_skill_pipeline(
    *,
    name: str,
    description: str,
    reason: str,
    requested_by: str,
    tenant_id: str = "default",
    risk: str = "low",
    side_effect: str = "none",
    handler_kind: str = "echo",
    input_schema: Optional[dict] = None,
    auto_approve_low_risk: bool = True,
) -> dict:
    """
    Full governed pipeline for agents:
    detect → propose → validate → (approve if needed) → install → test.
    """
    missing = detect_missing_capability(name)
    if not missing["missing"]:
        return {
            "ok": True,
            "already_present": True,
            "detection": missing,
            "proposal": None,
        }

    prop = propose_skill(
        name=name,
        description=description,
        reason=reason,
        requested_by=requested_by,
        tenant_id=tenant_id,
        risk=risk,
        side_effect=side_effect,
        handler_kind=handler_kind,
        input_schema=input_schema,
    )
    prop = validate_proposal(prop.proposal_id)
    if prop.status == ProposalStatus.REJECTED:
        return {"ok": False, "stage": "validate", "proposal": prop.to_dict()}

    if requires_hitl(prop.definition):
        prop = request_approval(prop.proposal_id, user_id=requested_by)
        return {
            "ok": False,
            "stage": "pending_approval",
            "proposal": prop.to_dict(),
            "message": "Privileged skill requires human approval before installation",
        }

    if auto_approve_low_risk:
        prop = install_skill(prop.proposal_id)
    return {
        "ok": prop.status == ProposalStatus.INSTALLED,
        "stage": prop.status.value,
        "proposal": prop.to_dict(),
        "detection": missing,
    }
