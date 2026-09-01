"""Production reliability primitives — Governance v1 is frozen; this layer
supports persistence, idempotency, secrets hygiene, correlation, and quotas.

Does NOT grant authority. Authority remains Identity + UCI + PathClass.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.reliability")

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

REDIS_URL = os.environ.get("DEVOS_REDIS_URL") or os.environ.get("REDIS_URL") or ""


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
    """Stable operation identity for side-effecting work."""
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
    """Recursively redact secret-like keys/values from durable structures."""
    if depth > 12:
        return REDACTED
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        if _SECRET_VALUE_RE.search(obj):
            return REDACTED
        if len(obj) > 40 and obj.isascii() and any(c in obj for c in ("=", "/", "+")):
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


AI_EFFECT_REQUIREMENTS = (
    "identity",
    "capability_authority",
    "isolation",
    "path_class",
    "execution_job_if_durable",
    "evidence_if_durable",
)


# ── Quotas ────────────────────────────────────────────────────────────────────

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
    backend: str = "memory"


_tenant_counters: dict[str, dict] = {}
_redis_quota = None


async def _redis():
    global _redis_quota
    if not REDIS_URL:
        return None
    if _redis_quota is not None:
        return _redis_quota
    try:
        import redis.asyncio as redis
        _redis_quota = redis.from_url(REDIS_URL, decode_responses=True)
        await _redis_quota.ping()
        return _redis_quota
    except Exception as e:
        logger.warning("quota redis unavailable: %s", e)
        _redis_quota = None
        return None


def check_quota(tenant_id: str, kind: str, *, increment: int = 1) -> QuotaDecision:
    """Sync in-process quota gate (tests + single-node)."""
    limits = DEFAULT_QUOTAS
    key = f"{tenant_id}:{kind}"
    bucket = _tenant_counters.setdefault(
        key, {"count": 0, "window_start": datetime.now(timezone.utc)}
    )
    if kind.endswith("_per_hour") or kind.endswith("_per_day"):
        window = 3600 if kind.endswith("_per_hour") else 86400
        if (datetime.now(timezone.utc) - bucket["window_start"]).total_seconds() > window:
            bucket["count"] = 0
            bucket["window_start"] = datetime.now(timezone.utc)
    limit = limits.get(kind)
    if limit is None:
        return QuotaDecision(True, "no_limit", backend="memory")
    if bucket["count"] + increment > limit:
        return QuotaDecision(False, f"quota exceeded: {kind}>={limit}", remaining=0, backend="memory")
    bucket["count"] += increment
    return QuotaDecision(True, "ok", remaining=max(0, limit - bucket["count"]), backend="memory")


# When multi-node is expected, Redis down must not multiply per-node memory quotas
MULTI_NODE = os.environ.get("DEVOS_MULTI_NODE", "").lower() in ("1", "true", "yes")
# Expensive operations fail closed if distributed quota is required but Redis is down
EXPENSIVE_QUOTA_KINDS = {
    "max_jobs_per_hour",
    "max_worker_spawns_per_hour",
    "max_tokens_per_day",
    "max_concurrent_jobs",
}


async def check_quota_async(tenant_id: str, kind: str, *, increment: int = 1) -> QuotaDecision:
    """Multi-node quota via Redis INCR when available.

    Redis unavailable + DEVOS_MULTI_NODE:
      expensive kinds → fail closed (degraded)
    else → conservative memory with reduced limits
    """
    limit = DEFAULT_QUOTAS.get(kind)
    if limit is None:
        return QuotaDecision(True, "no_limit", backend="none")
    r = await _redis()
    if r is None:
        if MULTI_NODE and kind in EXPENSIVE_QUOTA_KINDS:
            return QuotaDecision(
                False,
                f"quota degraded: redis unavailable for {kind} (multi-node fail-closed)",
                remaining=0,
                backend="degraded",
            )
        # Single-node: memory with tighter effective limit
        tight = max(1, limit // 2) if kind in EXPENSIVE_QUOTA_KINDS else limit
        # temporarily use tighter limit via counter
        key = f"{tenant_id}:{kind}"
        bucket = _tenant_counters.setdefault(
            key, {"count": 0, "window_start": datetime.now(timezone.utc)}
        )
        if kind.endswith("_per_hour") or kind.endswith("_per_day"):
            window = 3600 if kind.endswith("_per_hour") else 86400
            if (datetime.now(timezone.utc) - bucket["window_start"]).total_seconds() > window:
                bucket["count"] = 0
                bucket["window_start"] = datetime.now(timezone.utc)
        if bucket["count"] + increment > tight:
            return QuotaDecision(False, f"quota exceeded (conservative): {kind}>={tight}", remaining=0, backend="memory-conservative")
        bucket["count"] += increment
        return QuotaDecision(True, "ok", remaining=max(0, tight - bucket["count"]), backend="memory-conservative")
    window = 3600 if kind.endswith("_per_hour") else (86400 if kind.endswith("_per_day") else 60)
    slot = int(time.time() // window)
    key = f"devos:quota:{tenant_id}:{kind}:{slot}"
    try:
        count = await r.incrby(key, increment)
        if count == increment:
            await r.expire(key, window + 60)
        if count > limit:
            # roll back this increment for fairness
            await r.decrby(key, increment)
            return QuotaDecision(False, f"quota exceeded: {kind}>={limit}", remaining=0, backend="redis")
        return QuotaDecision(True, "ok", remaining=max(0, limit - count), backend="redis")
    except Exception as e:
        logger.warning("redis quota failed, memory fallback: %s", e)
        return check_quota(tenant_id, kind, increment=increment)


def reset_quota_counters():
    _tenant_counters.clear()


# ── Adversarial / integrity ───────────────────────────────────────────────────

def validate_tenant_scope(claimed_tenant_id: Optional[str], authenticated_tenant_id: Optional[str]) -> bool:
    if not authenticated_tenant_id:
        return False
    if claimed_tenant_id and claimed_tenant_id != authenticated_tenant_id:
        return False
    return True


def reject_authority_forgery(payload: dict) -> list[str]:
    banned = (
        "trust_level", "autonomy", "capability_token", "extra_caps",
        "actor_kind", "is_admin", "root", "AGENCY_OP", "authority_snapshot",
    )
    hits = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    banned_l = {b.lower() for b in banned}
    for k in payload.keys():
        kl = str(k).lower()
        if kl in banned_l:
            hits.append(str(k))
        if kl.endswith("_override") and "autonomy" in kl:
            hits.append(str(k))
    return hits


def assert_no_secrets_in_text(text: str) -> list[str]:
    """Return list of leaked secret patterns found in log/evidence text."""
    if not text:
        return []
    return [m.group(0)[:12] + "…" for m in _SECRET_VALUE_RE.finditer(text)]
