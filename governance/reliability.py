"""Production reliability primitives — Governance v1 is frozen; this layer
supports persistence, idempotency, secrets hygiene, and correlation.

Does NOT grant authority. Authority remains Identity + UCI + PathClass.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.reliability")

# Keys / patterns that must never enter durable evidence, job payloads, snapshots, logs
_SECRET_KEY_RE = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|credential|private[_-]?key|"
    r"jwt|bearer|access[_-]?key|session[_-]?key|client[_-]?secret)",
    re.I,
)
_SECRET_VALUE_RE = re.compile(
    r"(sk-[a-zA-Z0-9]{16,}|ghp_[a-zA-Z0-9]{20,}|Bearer\s+[A-Za-z0-9\-._~+/]+=*)",
    re.I,
)
REDACTED = "***REDACTED***"


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def new_idempotency_key(
    *,
    tenant_id: str,
    actor_id: str,
    capability: str,
    operation: str,
    body: Optional[dict] = None,
) -> str:
    """Stable operation identity for side-effecting work.

    Same logical operation → same key → at most one durable effect.
    """
    canonical = {
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "capability": capability,
        "operation": operation,
        "body": body or {},
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def scrub_secrets(obj: Any, *, depth: int = 0) -> Any:
    """Recursively redact secret-like keys and values from structures.

    Safe for evidence bodies, job payloads, trust snapshots, logs.
    """
    if depth > 12:
        return REDACTED
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        if _SECRET_VALUE_RE.search(obj):
            return REDACTED
        if len(obj) > 40 and obj.isascii() and any(c in obj for c in ("=", "/", "+")):
            # Heuristic: long opaque tokens
            low = obj.lower()
            if any(x in low for x in ("key-", "token", "secret")):
                return REDACTED
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                out[ks] = REDACTED
            else:
                out[ks] = scrub_secrets(v, depth=depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [scrub_secrets(x, depth=depth + 1) for x in obj]
    return str(obj)[:500]


@dataclass
class CorrelationChain:
    """request → job → authority → worker → capability → evidence → trust."""
    request_id: str = field(default_factory=new_request_id)
    execution_job_id: Optional[str] = None
    authority_snapshot_id: Optional[str] = None
    worker_id: Optional[str] = None
    capability: Optional[str] = None
    evidence_id: Optional[str] = None
    trust_update_id: Optional[str] = None
    tenant_id: Optional[str] = None
    actor_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "execution_job_id": self.execution_job_id,
            "authority_snapshot_id": self.authority_snapshot_id,
            "worker_id": self.worker_id,
            "capability": self.capability,
            "evidence_id": self.evidence_id,
            "trust_update_id": self.trust_update_id,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }

    def bind_job(self, job_id: str) -> "CorrelationChain":
        self.execution_job_id = job_id
        return self

    def bind_evidence(self, evidence_id: str) -> "CorrelationChain":
        self.evidence_id = evidence_id
        return self


# AI effect invariant checklist (documented for freeze tests / auditors)
AI_EFFECT_REQUIREMENTS = (
    "identity",
    "capability_authority",
    "isolation",
    "path_class",
    "execution_job_if_durable",
    "evidence_if_durable",
)


# ── Quotas / abuse controls (tenant-scoped, in-process + overridable) ─────────

DEFAULT_QUOTAS = {
    "max_concurrent_jobs": 10,
    "max_jobs_per_hour": 200,
    "max_worker_spawns_per_hour": 50,
    "max_delegation_depth": 10,
    "max_autonomous_duration_s": 3600,
    "max_tokens_per_day": 2_000_000,
}


@dataclass
class QuotaDecision:
    allowed: bool
    reason: str = ""
    remaining: Optional[int] = None


_tenant_counters: dict[str, dict] = {}


def check_quota(tenant_id: str, kind: str, *, increment: int = 1) -> QuotaDecision:
    """Lightweight in-process quota gate. Replace with Redis for multi-node."""
    limits = DEFAULT_QUOTAS
    key = f"{tenant_id}:{kind}"
    bucket = _tenant_counters.setdefault(key, {"count": 0, "window_start": datetime.now(timezone.utc)})
    # simple hourly window for rate-ish quotas
    if kind.endswith("_per_hour"):
        if (datetime.now(timezone.utc) - bucket["window_start"]).total_seconds() > 3600:
            bucket["count"] = 0
            bucket["window_start"] = datetime.now(timezone.utc)
    limit = limits.get(kind)
    if limit is None:
        return QuotaDecision(True, "no_limit")
    if bucket["count"] + increment > limit:
        return QuotaDecision(False, f"quota exceeded: {kind}>={limit}", remaining=0)
    bucket["count"] += increment
    return QuotaDecision(True, "ok", remaining=max(0, limit - bucket["count"]))


def reset_quota_counters():
    _tenant_counters.clear()


# ── Adversarial / integrity helpers ───────────────────────────────────────────

def validate_tenant_scope(claimed_tenant_id: Optional[str], authenticated_tenant_id: Optional[str]) -> bool:
    """Reject client-supplied tenant_id that does not match auth context."""
    if not authenticated_tenant_id:
        return False
    if claimed_tenant_id and claimed_tenant_id != authenticated_tenant_id:
        return False
    return True


def reject_authority_forgery(payload: dict) -> list[str]:
    """Flag hostile fields that must never be accepted from clients."""
    banned = (
        "trust_level", "autonomy", "capability_token", "extra_caps",
        "actor_kind", "is_admin", "root", "AGENCY_OP", "authority_snapshot",
    )
    hits = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    for k in payload.keys():
        if str(k).lower() in {b.lower() for b in banned}:
            hits.append(str(k))
        if str(k).lower().endswith("_override") and "autonomy" in str(k).lower():
            hits.append(str(k))
    return hits
