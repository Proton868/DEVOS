"""
Unified DevOS capability / execution substrate (foundational layer).

Purpose
-------
Provide a single contract and invocation path that IDE, Flow, Preview/Runtime,
Nuha, tests, and automation can share — without creating parallel engines.

Authority
---------
UCIP + CapabilityRegistry remain the authorization boundary.
This module does NOT grant capabilities. It only:

1. Resolves capability contracts from the existing registry / tool adapters
2. Validates inputs against declared schemas
3. Authorizes via authorize_capability_slug (and identity context when present)
4. Dispatches to registered executors *after* authorization
5. Emits structured results suitable for evidence / observability adapters

Nuha (and any other surface) may *request* invocation; they cannot bypass
authorization by supplying elevated granted_capabilities from the client.

Non-goals (this foundational layer)
-----------------------------------
- Spatial OS UI
- Full n8n node catalog
- New agent runtime
- Replacing AgentRuntime tool dispatch (adapters can call into this later)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("devos.capability_substrate")


# ── Contract model ───────────────────────────────────────────────────────────


class InvocationStatus(str, Enum):
    DENIED = "denied"
    VALIDATION_FAILED = "validation_failed"
    AUTHORIZED = "authorized"
    EXECUTED = "executed"
    FAILED = "failed"
    NOT_IMPLEMENTED = "not_implemented"
    ERROR = "error"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    backoff_base_s: float = 1.0
    # Classification labels only at this layer; executors interpret.
    retry_on: tuple[str, ...] = ("timeout", "rate_limit", "provider_5xx")


@dataclass(frozen=True)
class FailureBehavior:
    """How callers should treat failures (executors must honor fail-closed auth)."""
    auth_deny: str = "fail_closed"
    validation_error: str = "fail_closed"
    timeout: str = "retry_if_policy_allows"
    unknown_outcome: str = "unknown_no_auto_retry"
    non_retryable: tuple[str, ...] = ("auth", "validation", "policy", "not_found")


@dataclass(frozen=True)
class CapabilityContract:
    """Unified capability contract consumed by all DevOS surfaces."""

    identity: str
    version: str
    name: str
    category: str
    description: str
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    """trust_required, risk, requires_hitl, requires_network, granted_slug"""
    credentials: dict = field(default_factory=dict)
    """required: list[str] of credential names; never values"""
    timeout_s: int = 30
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    observability: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    failure: FailureBehavior = field(default_factory=FailureBehavior)
    source: str = "registry"  # registry | agent_tool | synthetic
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "identity": self.identity,
            "version": self.version,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permissions": dict(self.permissions),
            "credentials": dict(self.credentials),
            "timeout_s": self.timeout_s,
            "retry": {
                "max_retries": self.retry.max_retries,
                "backoff_base_s": self.retry.backoff_base_s,
                "retry_on": list(self.retry.retry_on),
            },
            "observability": dict(self.observability),
            "evidence": dict(self.evidence),
            "failure": {
                "auth_deny": self.failure.auth_deny,
                "validation_error": self.failure.validation_error,
                "timeout": self.failure.timeout,
                "unknown_outcome": self.failure.unknown_outcome,
                "non_retryable": list(self.failure.non_retryable),
            },
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass
class InvocationContext:
    """Server-side invocation context. Grants must come from trusted identity."""

    tenant_id: str
    owner_id: str
    granted_capabilities: set[str] = field(default_factory=set)
    actor_type: str = "system"  # user | agent | nuha | system | test
    actor_id: str = "system"
    surface: str = "unknown"  # ide | flow | runtime | nuha | test | automation
    correlation_id: Optional[str] = None
    trust_level: Optional[str] = None
    # Explicitly forbid client-elevated authority markers
    client_supplied_grants: bool = False
    # Trusted server-side metadata only (e.g. project_id). Never from planner authority.
    metadata: dict = field(default_factory=dict)

    @property
    def world_id(self) -> str:
        """Canonical world identity (world_id ≡ tenant_id)."""
        return str(self.tenant_id or "").strip()

    def require_world_bound(self) -> None:
        """Fail closed if world/principal missing. Call before execution."""
        if not str(self.tenant_id or "").strip():
            raise ValueError("WORLD_REQUIRED: InvocationContext.tenant_id/world_id missing")
        if not str(self.owner_id or "").strip():
            raise ValueError("PRINCIPAL_REQUIRED: InvocationContext.owner_id missing")
        wid = str(self.tenant_id).strip()
        if ".." in wid or "/" in wid or chr(92) in wid or chr(0) in wid:
            raise ValueError(f"INVALID_WORLD: {wid!r}")

    def effective_grants(self) -> set[str]:
        if self.client_supplied_grants:
            # Fail closed: never treat client-provided grant lists as authoritative
            return set()
        return set(self.granted_capabilities or set())


@dataclass
class InvocationRequest:
    capability_id: str
    inputs: dict
    context: InvocationContext
    timeout_s: Optional[int] = None
    idempotency_key: Optional[str] = None
    dry_run: bool = False


@dataclass
class InvocationResult:
    status: InvocationStatus
    capability_id: str
    authorized: bool
    auth_reason: str
    outputs: Optional[dict] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    evidence_ref: Optional[str] = None
    duration_ms: float = 0.0
    contract_version: Optional[str] = None
    correlation_id: Optional[str] = None
    surface: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value if isinstance(self.status, InvocationStatus) else self.status,
            "capability_id": self.capability_id,
            "authorized": self.authorized,
            "auth_reason": self.auth_reason,
            "outputs": self.outputs,
            "error": self.error,
            "error_class": self.error_class,
            "evidence_ref": self.evidence_ref,
            "duration_ms": self.duration_ms,
            "contract_version": self.contract_version,
            "correlation_id": self.correlation_id,
            "surface": self.surface,
            "metadata": dict(self.metadata or {}),
        }


# ── Schema validation (stdlib only) ──────────────────────────────────────────


def validate_against_schema(inputs: dict, schema: Optional[dict]) -> tuple[bool, list[str]]:
    """Minimal JSON-Schema-like validation for required + property types."""
    if not schema:
        return True, []
    errors: list[str] = []
    data = inputs if isinstance(inputs, dict) else {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    for key in required:
        if key not in data or data[key] is None:
            errors.append(f"missing required input: {key}")
    props = schema.get("properties") or {}
    if isinstance(props, dict):
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        for key, prop in props.items():
            if key not in data or data[key] is None:
                continue
            if not isinstance(prop, dict):
                continue
            expected = prop.get("type")
            if not expected:
                continue
            py = type_map.get(expected)
            if py is None:
                continue
            val = data[key]
            if expected == "number" and isinstance(val, bool):
                errors.append(f"input {key!r} must be number")
            elif expected == "integer" and isinstance(val, bool):
                errors.append(f"input {key!r} must be integer")
            elif not isinstance(val, py):
                errors.append(f"input {key!r} must be {expected}")
    return (len(errors) == 0), errors


# ── Adapters: registry + agent tools → contracts ─────────────────────────────


def contract_from_descriptor(desc: Any) -> CapabilityContract:
    """Adapt CapabilityDescriptor → CapabilityContract."""
    risk = getattr(desc, "risk", None)
    risk_v = risk.value if hasattr(risk, "value") else str(risk or "medium")
    cat = getattr(desc, "category", None)
    cat_v = cat.value if hasattr(cat, "value") else str(cat or "system")
    timeout = int(getattr(desc, "timeout_s", 30) or 30)
    max_retries = int(getattr(desc, "max_retries", 0) or 0)
    return CapabilityContract(
        identity=str(desc.slug),
        version=str(getattr(desc, "version", "1.0.0") or "1.0.0"),
        name=str(getattr(desc, "name", desc.slug)),
        category=cat_v,
        description=str(getattr(desc, "description", "") or ""),
        input_schema=dict(getattr(desc, "input_schema", None) or {}),
        output_schema=dict(getattr(desc, "output_schema", None) or {}),
        permissions={
            "trust_required": getattr(desc, "trust_required", "operator"),
            "risk": risk_v,
            "requires_hitl": bool(getattr(desc, "requires_hitl", False)),
            "requires_network": bool(getattr(desc, "requires_network", False)),
            "is_reversible": bool(getattr(desc, "is_reversible", True)),
        },
        credentials={
            "required": list((getattr(desc, "metadata", None) or {}).get("credentials") or []),
        },
        timeout_s=timeout,
        retry=RetryPolicy(max_retries=max_retries, backoff_base_s=1.0),
        observability={
            "emit_events": True,
            "scrub_secrets": True,
            "surface_agnostic": True,
        },
        evidence={
            "required": risk_v in ("high", "critical")
            or bool(getattr(desc, "requires_hitl", False)),
            "path_class": "durable"
            if risk_v in ("high", "critical")
            else "standard",
        },
        failure=FailureBehavior(),
        source="registry",
        metadata=dict(getattr(desc, "metadata", None) or {}),
    )


def contract_from_agent_tool(tool: Any) -> CapabilityContract:
    """Adapt AgentTool → CapabilityContract (capability may be None)."""
    cap = getattr(tool, "capability", None)
    name = str(getattr(tool, "name", "unknown_tool"))
    identity = str(cap) if cap else f"devos.tool.{name}"
    schema = getattr(tool, "parameters", None) or getattr(tool, "input_schema", None) or {}
    if not isinstance(schema, dict):
        schema = {}
    # Normalize tools that use JSON schema root
    if "properties" not in schema and "type" in schema:
        schema = schema
    return CapabilityContract(
        identity=identity,
        version="1.0.0",
        name=name,
        category="agent_tool",
        description=str(getattr(tool, "description", "") or ""),
        input_schema=dict(schema),
        output_schema={},
        permissions={
            "trust_required": "operator",
            "risk": "medium" if cap else "low",
            "requires_hitl": False,
            "requires_network": False,
            "tool_name": name,
            "ucip_slug": cap,
        },
        credentials={"required": []},
        timeout_s=60,
        retry=RetryPolicy(max_retries=0),
        observability={"emit_events": True, "scrub_secrets": True},
        evidence={"required": bool(cap), "path_class": "standard"},
        failure=FailureBehavior(),
        source="agent_tool",
        metadata={"tool_name": name},
    )


# ── Executor registry (pluggable; auth always first) ─────────────────────────

CapabilityExecutor = Callable[[CapabilityContract, InvocationRequest], Awaitable[dict]]


class CapabilitySubstrate:
    """
    Foundational capability substrate.

    Surfaces obtain contracts and request invocation; authorization is mandatory.
    """

    def __init__(self) -> None:
        self._executors: dict[str, CapabilityExecutor] = {}
        self._aliases: dict[str, str] = {}

    def register_executor(self, capability_id: str, executor: CapabilityExecutor) -> None:
        self._executors[str(capability_id)] = executor

    def register_alias(self, alias: str, capability_id: str) -> None:
        self._aliases[str(alias)] = str(capability_id)

    def resolve_id(self, capability_id: str) -> str:
        cid = str(capability_id or "").strip()
        if not cid:
            return cid
        # Normalize via UCIP ACTION_TO_CAP when present
        try:
            from governance.ucip import ACTION_TO_CAP

            if cid in ACTION_TO_CAP:
                cid = ACTION_TO_CAP[cid]
        except Exception:
            pass
        return self._aliases.get(cid, cid)

    def resolve(self, capability_id: str) -> Optional[CapabilityContract]:
        cid = self.resolve_id(capability_id)
        if not cid:
            return None
        # Registry first
        try:
            from governance.capability_registry import get_registry

            desc = get_registry().get(cid)
            if desc is not None:
                return contract_from_descriptor(desc)
        except Exception as e:
            logger.debug("registry resolve failed for %s: %s", cid, type(e).__name__)
        # Agent tool by name or by capability slug
        try:
            from brain.agent_tools import AGENT_TOOL_REGISTRY

            for tool in AGENT_TOOL_REGISTRY.values():
                tcap = getattr(tool, "capability", None)
                tname = getattr(tool, "name", None)
                if tcap == cid or tname == cid or f"devos.tool.{tname}" == cid:
                    return contract_from_agent_tool(tool)
        except Exception as e:
            logger.debug("agent tool resolve failed for %s: %s", cid, type(e).__name__)
        return None

    def list_contracts(
        self,
        *,
        category: Optional[str] = None,
        include_agent_tools: bool = True,
    ) -> list[CapabilityContract]:
        out: list[CapabilityContract] = []
        seen: set[str] = set()
        try:
            from governance.capability_registry import get_registry

            for desc in get_registry().list_all():
                c = contract_from_descriptor(desc)
                if category and c.category != category:
                    continue
                out.append(c)
                seen.add(c.identity)
        except Exception:
            pass
        if include_agent_tools:
            try:
                from brain.agent_tools import AGENT_TOOL_REGISTRY

                for tool in AGENT_TOOL_REGISTRY.values():
                    c = contract_from_agent_tool(tool)
                    if c.identity in seen:
                        continue
                    if category and c.category != category:
                        continue
                    out.append(c)
                    seen.add(c.identity)
            except Exception:
                pass
        return sorted(out, key=lambda c: c.identity)

    def authorize(
        self,
        contract: CapabilityContract,
        context: InvocationContext,
    ) -> tuple[bool, str]:
        """Authorize using canonical UCI slug check. Fail closed on client grants."""
        if context.client_supplied_grants:
            return False, "client_supplied_grants_rejected"
        slug = contract.identity
        # Prefer UCIP slug from agent tool permissions when present
        ucip_slug = (contract.permissions or {}).get("ucip_slug") or slug
        try:
            from governance.capability_registry import authorize_capability_slug

            ok, reason = authorize_capability_slug(str(ucip_slug), context.effective_grants())
            return bool(ok), str(reason)
        except Exception as e:
            logger.warning("authorize failed closed: %s", type(e).__name__)
            return False, f"authorize_error:{type(e).__name__}"

    def validate(
        self,
        contract: CapabilityContract,
        inputs: dict,
    ) -> tuple[bool, list[str]]:
        return validate_against_schema(inputs or {}, contract.input_schema)

    async def invoke(self, request: InvocationRequest) -> InvocationResult:
        """
        Authorize → validate → (optional) execute.

        dry_run=True stops after successful auth+validation with status AUTHORIZED.
        Missing executor → NOT_IMPLEMENTED (still requires auth).
        """
        t0 = time.perf_counter()
        cid = self.resolve_id(request.capability_id)
        corr = request.context.correlation_id or str(uuid.uuid4())
        surface = request.context.surface

        # --- World binding (fail closed) ---
        try:
            request.context.require_world_bound()
        except ValueError as ve:
            return InvocationResult(
                status=InvocationStatus.DENIED,
                capability_id=cid,
                authorized=False,
                auth_reason=str(ve),
                error=str(ve),
                error_class="WORLD_REQUIRED",
                duration_ms=0.0,
                correlation_id=corr,
                surface=surface,
            )

        def _finish(
            status: InvocationStatus,
            *,
            authorized: bool,
            auth_reason: str,
            outputs: Optional[dict] = None,
            error: Optional[str] = None,
            error_class: Optional[str] = None,
            contract: Optional[CapabilityContract] = None,
            metadata: Optional[dict] = None,
        ) -> InvocationResult:
            return InvocationResult(
                status=status,
                capability_id=cid,
                authorized=authorized,
                auth_reason=auth_reason,
                outputs=outputs,
                error=error,
                error_class=error_class,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                contract_version=contract.version if contract else None,
                correlation_id=corr,
                surface=surface,
                metadata=metadata or {},
            )

        contract = self.resolve(cid)
        if contract is None:
            return _finish(
                InvocationStatus.FAILED,
                authorized=False,
                auth_reason="unknown_capability",
                error=f"unknown capability: {cid}",
                error_class="not_found",
            )

        ok, reason = self.authorize(contract, request.context)
        if not ok:
            logger.info(
                "capability denied id=%s surface=%s actor=%s/%s reason=%s",
                cid,
                surface,
                request.context.actor_type,
                request.context.actor_id,
                reason,
            )
            return _finish(
                InvocationStatus.DENIED,
                authorized=False,
                auth_reason=reason,
                error="not authorized",
                error_class="auth",
                contract=contract,
            )

        valid, errors = self.validate(contract, request.inputs or {})
        if not valid:
            return _finish(
                InvocationStatus.VALIDATION_FAILED,
                authorized=True,
                auth_reason=reason,
                error="; ".join(errors),
                error_class="validation",
                contract=contract,
            )

        if request.dry_run:
            return _finish(
                InvocationStatus.AUTHORIZED,
                authorized=True,
                auth_reason=reason,
                outputs={"dry_run": True, "inputs": request.inputs or {}},
                contract=contract,
            )

        executor = self._executors.get(cid) or self._executors.get(contract.identity)
        if executor is None:
            return _finish(
                InvocationStatus.NOT_IMPLEMENTED,
                authorized=True,
                auth_reason=reason,
                error="no executor registered for capability (foundation layer)",
                error_class="not_implemented",
                contract=contract,
                metadata={"hint": "register_executor after authorization path"},
            )

        try:
            result = await executor(contract, request)
            if not isinstance(result, dict):
                result = {"result": result}
            return _finish(
                InvocationStatus.EXECUTED,
                authorized=True,
                auth_reason=reason,
                outputs=result,
                contract=contract,
            )
        except Exception as e:
            logger.exception("capability executor failed id=%s", cid)
            return _finish(
                InvocationStatus.ERROR,
                authorized=True,
                auth_reason=reason,
                error=str(e)[:500],
                error_class="executor_error",
                contract=contract,
            )


_substrate: Optional[CapabilitySubstrate] = None


def get_capability_substrate() -> CapabilitySubstrate:
    global _substrate
    if _substrate is None:
        _substrate = CapabilitySubstrate()
        _install_builtin_executors(_substrate)
    return _substrate


def reset_capability_substrate_for_tests() -> CapabilitySubstrate:
    """Test helper: fresh substrate with builtin executors only."""
    global _substrate
    _substrate = CapabilitySubstrate()
    _install_builtin_executors(_substrate)
    return _substrate


def _install_builtin_executors(sub: CapabilitySubstrate) -> None:
    """
    Built-in executors that only expose safe meta-operations.
    Real side-effecting work continues to use existing runners; they can
    register_executor as surfaces adopt the substrate.
    """

    async def _list_caps(contract: CapabilityContract, req: InvocationRequest) -> dict:
        contracts = sub.list_contracts(include_agent_tools=True)
        return {
            "count": len(contracts),
            "capabilities": [
                {
                    "identity": c.identity,
                    "name": c.name,
                    "category": c.category,
                    "version": c.version,
                    "source": c.source,
                }
                for c in contracts[:500]
            ],
        }

    async def _resolve_cap(contract: CapabilityContract, req: InvocationRequest) -> dict:
        target = (req.inputs or {}).get("capability_id") or (req.inputs or {}).get("slug")
        resolved = sub.resolve(str(target or ""))
        if resolved is None:
            return {"found": False, "capability_id": target}
        return {"found": True, "contract": resolved.to_dict()}

    # Synthetic meta-capabilities (always need explicit grant of these ids or *)
    sub.register_executor("devos.capability.list", _list_caps)
    sub.register_executor("devos.capability.resolve", _resolve_cap)


def ensure_meta_contracts_registered() -> None:
    """
    Register lightweight synthetic descriptors for meta capabilities if missing.
    Does not remove or alter existing UCIP builtins.
    """
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        reg = get_registry()
        meta = [
            CapabilityDescriptor(
                slug="devos.capability.list",
                name="List Capability Contracts",
                category=CapabilityCategory.SYSTEM,
                description="List unified capability contracts (meta; no side effects)",
                risk=CapabilityRisk.LOW,
                trust_required="read_only",
                timeout_s=10,
                max_retries=0,
                input_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "required": ["count", "capabilities"],
                    "properties": {
                        "count": {"type": "integer"},
                        "capabilities": {"type": "array"},
                    },
                },
            ),
            CapabilityDescriptor(
                slug="devos.capability.resolve",
                name="Resolve Capability Contract",
                category=CapabilityCategory.SYSTEM,
                description="Resolve a capability id to its unified contract",
                risk=CapabilityRisk.LOW,
                trust_required="read_only",
                timeout_s=10,
                max_retries=0,
                input_schema={
                    "type": "object",
                    "required": ["capability_id"],
                    "properties": {"capability_id": {"type": "string"}},
                },
                output_schema={
                    "type": "object",
                    "required": ["found"],
                    "properties": {"found": {"type": "boolean"}},
                },
            ),
        ]
        for d in meta:
            if reg.get(d.slug) is None:
                reg.register(d)
    except Exception as e:
        logger.debug("meta contract registration skipped: %s", type(e).__name__)


# Register meta contracts at import when registry is available
try:
    ensure_meta_contracts_registered()
except Exception:
    pass

# Runtime lifecycle capabilities (Preview / IDE / Flow / Nuha)
try:
    from governance.runtime_capabilities import ensure_runtime_capabilities_registered
    ensure_runtime_capabilities_registered()
except Exception:
    pass

