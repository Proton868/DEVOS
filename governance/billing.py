"""
Enterprise — Billing Hooks (Agency OS Master Plan §7).

Lightweight billing hooks that fire on capability usage, LLM token consumption,
and execution time. In Micro profile, these are no-ops that log to audit.
In Standard/Enterprise profiles, they integrate with Stripe or custom billing.

Billing is tracked per-tenant, per-capability. The billing engine is designed
to be pluggable — swap the backend without changing the interface.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.billing")


class BillingBackend(str, Enum):
    NONE = "none"        # Micro profile: no-op
    AUDIT = "audit"      # Log to audit only
    STRIPE = "stripe"    # Stripe integration
    CUSTOM = "custom"    # Custom webhook


@dataclass
class BillingEvent:
    """A single billable event."""
    tenant_id: str
    event_type: str
    amount: float = 0.0
    currency: str = "usd"
    description: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "amount": self.amount,
            "currency": self.currency,
            "description": self.description,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class BillingUsage:
    """Current usage snapshot for a tenant."""
    tenant_id: str
    llm_tokens: int = 0
    llm_cost: float = 0.0
    execution_seconds: float = 0.0
    execution_cost: float = 0.0
    api_calls: int = 0
    api_cost: float = 0.0
    storage_bytes: int = 0
    storage_cost: float = 0.0
    total_cost: float = 0.0
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "llm_tokens": self.llm_tokens,
            "llm_cost": round(self.llm_cost, 6),
            "execution_seconds": round(self.execution_seconds, 2),
            "execution_cost": round(self.execution_cost, 6),
            "api_calls": self.api_calls,
            "api_cost": round(self.api_cost, 6),
            "storage_bytes": self.storage_bytes,
            "storage_cost": round(self.storage_cost, 6),
            "total_cost": round(self.total_cost, 6),
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
        }


# Pricing tiers (all in USD)
PRICING = {
    "llm_token_1k": 0.00001,     # $0.00001 per 1K tokens
    "execution_second": 0.0001,   # $0.0001 per second of execution time
    "api_call": 0.001,            # $0.001 per API call
    "storage_gb_month": 0.02,     # $0.02 per GB per month
}


class BillingEngine:
    """Pluggable billing engine.

    Backend options:
      - NONE: no-op (Micro profile)
      - AUDIT: log to audit logger (Standard profile)
      - STRIPE: integrate with Stripe (Enterprise profile)
      - CUSTOM: call a webhook (Enterprise profile)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._backend = BillingBackend.NONE
        self._usage: dict[str, BillingUsage] = {}
        self._events: list[BillingEvent] = []
        self._stripe_key: Optional[str] = None
        self._custom_url: Optional[str] = None
        self._initialized = True
        logger.info("[billing] engine initialized (backend=none)")

    def configure(self, backend: BillingBackend,
                  stripe_key: Optional[str] = None,
                  custom_url: Optional[str] = None):
        """Configure the billing backend."""
        self._backend = backend
        self._stripe_key = stripe_key
        self._custom_url = custom_url
        logger.info(f"[billing] configured backend={backend.value}")

    def record_llm_tokens(self, tenant_id: str, tokens: int,
                          model: str = "", provider: str = ""):
        """Record LLM token usage."""
        cost = (tokens / 1000) * PRICING["llm_token_1k"]
        self._ensure_usage(tenant_id)
        usage = self._usage[tenant_id]
        usage.llm_tokens += tokens
        usage.llm_cost += cost
        usage.total_cost += cost

        event = BillingEvent(
            tenant_id=tenant_id,
            event_type="llm_tokens",
            amount=cost,
            description=f"{tokens} tokens via {provider}/{model}",
            metadata={"tokens": tokens, "model": model, "provider": provider},
        )
        self._fire(event)

    def record_execution(self, tenant_id: str, seconds: float,
                         language: str = ""):
        """Record execution time."""
        cost = seconds * PRICING["execution_second"]
        self._ensure_usage(tenant_id)
        usage = self._usage[tenant_id]
        usage.execution_seconds += seconds
        usage.execution_cost += cost
        usage.total_cost += cost

        event = BillingEvent(
            tenant_id=tenant_id,
            event_type="execution",
            amount=cost,
            description=f"{seconds:.1f}s execution ({language})",
            metadata={"seconds": seconds, "language": language},
        )
        self._fire(event)

    def record_api_call(self, tenant_id: str, endpoint: str = ""):
        """Record an API call."""
        cost = PRICING["api_call"]
        self._ensure_usage(tenant_id)
        usage = self._usage[tenant_id]
        usage.api_calls += 1
        usage.api_cost += cost
        usage.total_cost += cost

        event = BillingEvent(
            tenant_id=tenant_id,
            event_type="api_call",
            amount=cost,
            description=f"API call: {endpoint}",
            metadata={"endpoint": endpoint},
        )
        self._fire(event)

    def record_storage(self, tenant_id: str, bytes_used: int):
        """Record storage usage."""
        cost = (bytes_used / (1024**3)) * PRICING["storage_gb_month"]
        self._ensure_usage(tenant_id)
        usage = self._usage[tenant_id]
        usage.storage_bytes = bytes_used
        usage.storage_cost = cost

    def get_usage(self, tenant_id: str) -> BillingUsage:
        """Get current usage for a tenant."""
        self._ensure_usage(tenant_id)
        return self._usage[tenant_id]

    def reset_usage(self, tenant_id: str):
        """Reset usage for a billing period."""
        self._usage[tenant_id] = BillingUsage(
            tenant_id=tenant_id,
            period_start=datetime.now(timezone.utc),
        )

    def _ensure_usage(self, tenant_id: str):
        if tenant_id not in self._usage:
            self._usage[tenant_id] = BillingUsage(
                tenant_id=tenant_id,
                period_start=datetime.now(timezone.utc),
            )

    def _fire(self, event: BillingEvent):
        """Fire the billing event to the configured backend."""
        self._events.append(event)

        if self._backend == BillingBackend.NONE:
            pass
        elif self._backend == BillingBackend.AUDIT:
            from governance.audit import get_audit_logger, AuditEventType
            get_audit_logger().log(
                event_type=AuditEventType.BILLING_EVENT,
                actor_id="system",
                tenant_id=event.tenant_id,
                action=event.event_type,
                target="billing",
                outcome="recorded",
                details=event.to_dict(),
            )
        elif self._backend == BillingBackend.STRIPE:
            # Stripe integration placeholder
            logger.debug(f"[billing] stripe event: {event.to_dict()}")
        elif self._backend == BillingBackend.CUSTOM:
            # Custom webhook placeholder
            logger.debug(f"[billing] custom webhook: {event.to_dict()}")

    def list_events(self, tenant_id: Optional[str] = None,
                    limit: int = 50) -> list[dict]:
        """List recent billing events."""
        events = self._events
        if tenant_id:
            events = [e for e in events if e.tenant_id == tenant_id]
        return [e.to_dict() for e in events[-limit:]]


def get_billing() -> BillingEngine:
    return BillingEngine()