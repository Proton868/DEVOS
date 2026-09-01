"""External side-effect reliability.

Internal job idempotency ≠ external provider exactly-once.

Outcomes:
  SUCCEEDED  — confirmed external effect + local record
  FAILED     — confirmed no effect or safe to retry
  UNKNOWN    — effect may have occurred; DO NOT blind-retry

For UNKNOWN: reconcile via provider query before any retry.
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


@dataclass
class SideEffectRecord:
    """Tracks one external mutation under a DevOS + provider idempotency pair."""
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
        """Blind retry is only safe on confirmed FAILED."""
        return self.outcome == EffectOutcome.FAILED

    @property
    def requires_reconcile(self) -> bool:
        return self.outcome == EffectOutcome.UNKNOWN


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
    """Run an external mutation conservatively.

    1. Call provider (pass provider_idempotency_key when supported)
    2. On clean success → SUCCEEDED
    3. On clean failure before send → FAILED (retry ok)
    4. On timeout / crash window / ambiguous response → UNKNOWN
       If reconcile provided, query provider before deciding
    """
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
        status = (result or {}).get("status") or (result or {}).get("outcome")
        if status in ("success", "succeeded", "ok", "created"):
            rec.outcome = EffectOutcome.SUCCEEDED
            rec.external_ref = (result or {}).get("id") or (result or {}).get("external_ref")
            rec.metadata = {k: v for k, v in (result or {}).items() if k not in ("status", "outcome")}
            return rec
        if status in ("failed", "error", "rejected"):
            rec.outcome = EffectOutcome.FAILED
            rec.error = str((result or {}).get("error") or status)
            return rec
        # Ambiguous provider response
        rec.outcome = EffectOutcome.UNKNOWN
        rec.metadata = {"raw": result}
    except TimeoutError as e:
        rec.outcome = EffectOutcome.UNKNOWN
        rec.error = f"timeout: {e}"
    except Exception as e:
        # Network error before confirmation is often UNKNOWN, not FAILED
        name = type(e).__name__
        if name in ("TimeoutError", "ConnectTimeout", "ReadTimeout", "APITimeoutError"):
            rec.outcome = EffectOutcome.UNKNOWN
            rec.error = str(e)
        elif "connection" in str(e).lower() or "timeout" in str(e).lower():
            rec.outcome = EffectOutcome.UNKNOWN
            rec.error = str(e)
        else:
            # Local validation errors before external call → FAILED
            if "validation" in str(e).lower() or name in ("ValueError", "TypeError"):
                rec.outcome = EffectOutcome.FAILED
                rec.error = str(e)
            else:
                rec.outcome = EffectOutcome.UNKNOWN
                rec.error = str(e)

    if rec.outcome == EffectOutcome.UNKNOWN and reconcile is not None:
        try:
            found = await reconcile()
            if found is None:
                # Provider confirms absence → safe to treat as FAILED for retry
                rec.outcome = EffectOutcome.FAILED
                rec.error = (rec.error or "") + " | reconcile: not found"
            else:
                rec.outcome = EffectOutcome.SUCCEEDED
                rec.external_ref = (found or {}).get("id") or rec.external_ref
                rec.metadata["reconciled"] = True
        except Exception as re_err:
            logger.warning("reconcile failed for %s: %s", operation, re_err)
            # stay UNKNOWN
    return rec


def should_retry_job_after_effect(rec: SideEffectRecord) -> bool:
    """Queue/retry policy helper — UNKNOWN must not auto-retry external ops."""
    if rec.outcome == EffectOutcome.FAILED:
        return True
    if rec.outcome == EffectOutcome.SUCCEEDED:
        return False
    return False  # UNKNOWN: hold for human/reconcile
