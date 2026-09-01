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
