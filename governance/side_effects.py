"""External side-effect reliability.

Internal job idempotency ≠ external provider exactly-once.

Outcomes:
  SUCCEEDED  — confirmed external effect + local record
  FAILED     — confirmed no effect OR safe local pre-send error (retry ok)
  UNKNOWN    — effect may have occurred; DO NOT blind-retry

Knowledge of the external effect drives classification — not HTTP status alone.
HTTP 500 / connection reset / timeout after request may leave the effect done.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Callable, Awaitable

logger = logging.getLogger("devos.side_effects")


class EffectOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


# HTTP statuses that mean "request not accepted / effect did not happen"
# Only use when the client is certain the body never reached processing.
_HTTP_DEFINITE_NO_EFFECT = {
    400, 401, 403, 404, 405, 406, 409, 410, 411, 412, 413, 414, 415, 422, 429,
}

# Anything 5xx or ambiguous transport → UNKNOWN unless reconcile proves otherwise
_HTTP_AMBIGUOUS = set(range(500, 600)) | {408, 425, 449}


@dataclass
class SideEffectRecord:
    operation: str
    tenant_id: str
    actor_id: str
    idempotency_key: str
    provider_idempotency_key: Optional[str] = None
    outcome: EffectOutcome = EffectOutcome.UNKNOWN
    provider: Optional[str] = None
    external_ref: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "idempotency_key": self.idempotency_key,
            "provider_idempotency_key": self.provider_idempotency_key,
            "outcome": self.outcome.value,
            "provider": self.provider,
            "external_ref": self.external_ref,
            "error": self.error,
            "metadata": self.metadata,
            "recorded_at": self.recorded_at,
        }

    @property
    def may_retry(self) -> bool:
        return self.outcome == EffectOutcome.FAILED

    @property
    def requires_reconcile(self) -> bool:
        return self.outcome == EffectOutcome.UNKNOWN


def classify_provider_result(result: Optional[dict]) -> EffectOutcome:
    """Classify provider return value conservatively.

    Explicit success statuses → SUCCEEDED
    Client errors that refuse the request before processing → FAILED
    5xx / timeout-like / missing status → UNKNOWN
    """
    if not isinstance(result, dict):
        return EffectOutcome.UNKNOWN
    status = result.get("status") or result.get("outcome")
    if status in ("success", "succeeded", "ok", "created", "accepted"):
        return EffectOutcome.SUCCEEDED
    if status in ("rejected", "invalid", "validation_error"):
        return EffectOutcome.FAILED
    http = result.get("http_status") or result.get("status_code")
    if isinstance(http, int):
        if 200 <= http < 300:
            return EffectOutcome.SUCCEEDED
        if http in _HTTP_DEFINITE_NO_EFFECT:
            return EffectOutcome.FAILED
        if http in _HTTP_AMBIGUOUS or http >= 500:
            return EffectOutcome.UNKNOWN
        # other 4xx: prefer FAILED (request rejected)
        if 400 <= http < 500:
            return EffectOutcome.FAILED
    if status in ("failed", "error"):
        # Without http_status, "error" after a possible send is UNKNOWN
        if result.get("request_sent") is True:
            return EffectOutcome.UNKNOWN
        if result.get("request_sent") is False:
            return EffectOutcome.FAILED
        return EffectOutcome.UNKNOWN
    return EffectOutcome.UNKNOWN


def classify_exception(exc: BaseException) -> EffectOutcome:
    """Local pre-send errors → FAILED; transport/timeout → UNKNOWN."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if name in ("ValueError", "TypeError", "KeyError") or "validation" in msg:
        return EffectOutcome.FAILED
    if name in (
        "TimeoutError", "ConnectTimeout", "ReadTimeout", "APITimeoutError",
        "ConnectionError", "ConnectError", "RemoteProtocolError",
    ):
        return EffectOutcome.UNKNOWN
    if any(x in msg for x in ("timeout", "connection reset", "broken pipe", "tls", "ssl")):
        return EffectOutcome.UNKNOWN
    # Default conservative
    return EffectOutcome.UNKNOWN


async def execute_side_effect(
    *,
    operation: str,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    provider_idempotency_key: Optional[str],
    call: Callable[[], Awaitable[dict]],
    reconcile: Optional[Callable[[], Awaitable[Optional[dict]]]] = None,
    provider: Optional[str] = None,
) -> SideEffectRecord:
    rec = SideEffectRecord(
        operation=operation,
        tenant_id=tenant_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        provider_idempotency_key=provider_idempotency_key,
        provider=provider,
    )
    try:
        result = await call()
        rec.outcome = classify_provider_result(result)
        if rec.outcome == EffectOutcome.SUCCEEDED:
            rec.external_ref = (result or {}).get("id") or (result or {}).get("external_ref")
            rec.metadata = {
                k: v for k, v in (result or {}).items()
                if k not in ("status", "outcome", "http_status", "status_code")
            }
            return rec
        if rec.outcome == EffectOutcome.FAILED:
            rec.error = str((result or {}).get("error") or (result or {}).get("status") or "failed")
            return rec
        rec.metadata = {"raw": result}
    except Exception as e:
        rec.outcome = classify_exception(e)
        rec.error = str(e)
        if rec.outcome == EffectOutcome.FAILED:
            return rec

    if rec.outcome == EffectOutcome.UNKNOWN and reconcile is not None:
        try:
            found = await reconcile()
            if found is None:
                rec.outcome = EffectOutcome.FAILED
                rec.error = (rec.error or "") + " | reconcile: not found"
            else:
                rec.outcome = EffectOutcome.SUCCEEDED
                rec.external_ref = (found or {}).get("id") or rec.external_ref
                rec.metadata["reconciled"] = True
        except Exception as re_err:
            logger.warning("reconcile failed for %s: %s", operation, re_err)
    return rec


def should_retry_job_after_effect(rec: SideEffectRecord) -> bool:
    if rec.outcome == EffectOutcome.FAILED:
        return True
    return False
