"""
DevOS HITL — Human-in-the-Loop Control Gate (Gap #10 from audit).
═══════════════════════════════════════════════════════════════════════════════
Implements approval gates for:
  - File deletion (ucip:filesystem.delete)
  - System commands (ucip:system.shell)
  - Agent spawning (ucip:agent.spawn)
  - External API calls with side effects
  - Any action the Brain itself flags as irreversible

Flow:
  Brain requests action → UCIP evaluates → ESCALATE decision
  → HITLQueue.submit(pending_action)
  → SSE event pushed to frontend ("approval required")
  → User sees: action description, code/input preview, APPROVE / DENY buttons
  → HITLQueue.resolve(id, approved=True/False)
  → Loop resumes or aborts

The loop is PAUSED while waiting. Timeout after N seconds → auto-deny.
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

logger = logging.getLogger("devos.hitl")

# Auto-deny timeout (seconds). 0 = no timeout.
HITL_TIMEOUT_S = 120


@dataclass
class HITLRequest:
    id:           str
    loop_id:      str
    agent_id:     str
    user_id:      str
    action:       str
    action_input: str
    description:  str
    cap_required: str
    reason:       str
    status:       str       # pending | approved | denied | timeout
    submitted_at: datetime  = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at:  Optional[datetime] = None
    resolved_by:  Optional[str] = None   # user_id who resolved

    def is_pending(self) -> bool:
        return self.status == "pending"

    def is_expired(self) -> bool:
        if HITL_TIMEOUT_S <= 0:
            return False
        return (datetime.now(timezone.utc) - self.submitted_at).total_seconds() > HITL_TIMEOUT_S

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "loop_id":      self.loop_id,
            "agent_id":     self.agent_id,
            "user_id":      self.user_id,
            "action":       self.action,
            "action_input": self.action_input[:500],
            "description":  self.description,
            "cap_required": self.cap_required,
            "reason":       self.reason,
            "status":       self.status,
            "submitted_at": self.submitted_at.isoformat(),
            "resolved_at":  self.resolved_at.isoformat() if self.resolved_at else None,
            "expires_in_s": max(0, HITL_TIMEOUT_S - int((datetime.now(timezone.utc) - self.submitted_at).total_seconds())) if HITL_TIMEOUT_S > 0 else None,
        }


class HITLQueue:
    """
    Manages pending human-approval requests.
    Singleton per application.
    Loops pause on asyncio.Event until resolved or timed out.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._requests: dict[str, HITLRequest] = {}
                    cls._instance._events:   dict[str, asyncio.Event] = {}
                    cls._instance._push_callbacks: list[Callable] = []
        return cls._instance

    def on_new_request(self, callback: Callable):
        """Register a callback fired when a new HITL request arrives (for SSE push)."""
        self._push_callbacks.append(callback)

    def _notify_callbacks(self, request: HITLRequest):
        for cb in self._push_callbacks:
            try:
                cb(request)
            except Exception as e:
                logger.debug(f"HITL callback error: {e}")

    async def submit(self, loop_id: str, agent_id: str, user_id: str,
                     action: str, action_input: str, description: str,
                     cap_required: str, reason: str) -> HITLRequest:
        """
        Submit an action for human approval.
        Returns HITLRequest. Caller should then await wait_for_decision(request.id).
        """
        req = HITLRequest(
            id=str(uuid.uuid4()),
            loop_id=loop_id, agent_id=agent_id, user_id=user_id,
            action=action, action_input=action_input,
            description=description, cap_required=cap_required,
            reason=reason, status="pending",
        )
        self._requests[req.id] = req
        self._events[req.id] = asyncio.Event()
        logger.info(f"[HITL] Submitted: {req.id} action={action} user={user_id}")
        self._notify_callbacks(req)
        from communications.bus import EventBus
        await EventBus().publish(f"user:{user_id}", "hitl.pending", req.to_dict())
        return req

    async def wait_for_decision(self, request_id: str) -> bool:
        """
        Pauses the calling coroutine until the request is resolved or times out.
        Returns True if approved, False if denied or timed out.
        """
        event = self._events.get(request_id)
        if not event:
            return False

        if HITL_TIMEOUT_S > 0:
            try:
                await asyncio.wait_for(event.wait(), timeout=float(HITL_TIMEOUT_S))
            except asyncio.TimeoutError:
                req = self._requests.get(request_id)
                if req and req.is_pending():
                    req.status = "timeout"
                    req.resolved_at = datetime.now(timezone.utc)
                    logger.warning(f"[HITL] Timeout: {request_id}")
                return False
        else:
            await event.wait()

        req = self._requests.get(request_id)
        if not req:
            return False
        return req.status == "approved"

    def resolve(self, request_id: str, approved: bool,
                resolved_by: Optional[str] = None) -> Optional[HITLRequest]:
        req = self._requests.get(request_id)
        if not req:
            return None
        if not req.is_pending():
            return req  # Already resolved

        req.status = "approved" if approved else "denied"
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = resolved_by

        event = self._events.get(request_id)
        if event:
            event.set()

        logger.info(f"[HITL] {'Approved' if approved else 'Denied'}: {request_id} by {resolved_by}")
        try:
            from communications.bus import EventBus
            asyncio.create_task(EventBus().publish(f"user:{req.user_id}", "hitl.resolved", req.to_dict()))
        except RuntimeError:
            pass  # no running loop (e.g. called from a sync test) — safe to skip
        return req

    def get_pending(self, user_id: Optional[str] = None) -> list[dict]:
        """Get all pending requests, optionally filtered by user."""
        now = datetime.now(timezone.utc)
        result = []
        for req in self._requests.values():
            # Auto-expire
            if req.is_pending() and req.is_expired():
                req.status = "timeout"
                req.resolved_at = now
                event = self._events.get(req.id)
                if event:
                    event.set()
                continue
            if req.is_pending():
                if user_id is None or req.user_id == user_id:
                    result.append(req.to_dict())
        return result

    def get_history(self, user_id: Optional[str] = None,
                    limit: int = 50) -> list[dict]:
        """Get resolved requests."""
        resolved = [r for r in self._requests.values()
                    if r.status != "pending"
                    and (user_id is None or r.user_id == user_id)]
        resolved.sort(key=lambda r: r.submitted_at, reverse=True)
        return [r.to_dict() for r in resolved[:limit]]

    def stats(self) -> dict:
        all_reqs = list(self._requests.values())
        return {
            "total":    len(all_reqs),
            "pending":  sum(1 for r in all_reqs if r.status == "pending"),
            "approved": sum(1 for r in all_reqs if r.status == "approved"),
            "denied":   sum(1 for r in all_reqs if r.status == "denied"),
            "timeout":  sum(1 for r in all_reqs if r.status == "timeout"),
        }
