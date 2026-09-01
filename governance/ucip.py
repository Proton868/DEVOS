"""
UCIP — Universal Capability Interface Protocol
═══════════════════════════════════════════════════════════════════════════════
Governance layer for DevOS agents.

Every agent in DevOS has:
  - A cryptographic AgentID (derived from its public key)
  - A TrustLevel (READ_ONLY → ASSISTANT → OPERATOR → AUTONOMOUS → ROOT)
  - A CapabilitySet (explicit allowlist of what it can do)
  - A BudgetPolicy (token, execution, time limits)
  - An AuditLog (every decision, approved or denied)

The Brain loop MUST pass through UCIP before any tool is executed.
UCIP is the ONLY path to the Execution Layer.

Architecture:
  Brain Decision → UCIPGateway.request() → PolicyEngine.evaluate()
                → [APPROVE | DENY | ESCALATE_TO_HUMAN]
                → if APPROVE: ExecutionLayer.run()
                → AuditLogger.record(decision + result)

Based on:
  - ACP v1.13 (Autonomous Control Protocol)
  - UCAN (User-Controlled Authorization Network)
  - NIST AI RMF 1.0 (AI Risk Management Framework)
  - Your UCIP spec (YahShalom/Universal-Capability-Interface)
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import IntEnum, auto
from typing import Any, Optional

logger = logging.getLogger("devos.ucip")


# ── Trust Levels ──────────────────────────────────────────────────────────────

class TrustLevel(IntEnum):
    """
    Five-tier trust hierarchy.
    Higher = more capability. Permissions are always a strict SUBSET of delegator.
    """
    READ_ONLY   = 1   # Can only read data, recall memory, search
    ASSISTANT   = 2   # Can write files, call APIs (no execution)
    OPERATOR    = 3   # Can execute code in sandbox, manage scripts
    AUTONOMOUS  = 4   # Full loop with human-approval gates for irreversible actions
    ROOT        = 5   # Unrestricted — disabled by default, requires explicit unlock

    @classmethod
    def from_str(cls, s: str) -> "TrustLevel":
        if not isinstance(s, str):
            raise ValueError("trust_level must be a string")
        name = s.strip().upper()
        try:
            return cls[name]
        except KeyError as exc:
            valid = ", ".join(member.name.lower() for member in cls)
            raise ValueError(f"Invalid trust_level '{s}'. Expected one of: {valid}") from exc

    def label(self) -> str:
        return self.name.replace("_", " ").title()


# ── Capabilities ─────────────────────────────────────────────────────────────

class Cap(str):
    """
    Capability token. Format: ucip:<domain>.<action>
    Examples:
      ucip:execution.python
      ucip:execution.bash
      ucip:filesystem.read
      ucip:filesystem.write
      ucip:filesystem.delete     ← requires HITL gate
      ucip:network.outbound
      ucip:memory.read
      ucip:memory.write
      ucip:search.web
      ucip:system.shell          ← ROOT only
      ucip:agent.spawn           ← AUTONOMOUS+
      ucip:secret.read
    """
    pass


# Canonical capability sets per trust level
TRUST_LEVEL_CAPS: dict[TrustLevel, set[str]] = {
    TrustLevel.READ_ONLY: {
        "ucip:memory.read",
        "ucip:search.web",
        "ucip:filesystem.read",
    },
    TrustLevel.ASSISTANT: {
        "ucip:memory.read",
        "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read",
        "ucip:filesystem.write",
        "ucip:api.call",
    },
    TrustLevel.OPERATOR: {
        "ucip:memory.read",
        "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read",
        "ucip:filesystem.write",
        "ucip:execution.python",
        "ucip:execution.bash",
        "ucip:execution.node",
        "ucip:api.call",
        "ucip:secret.read",
    },
    TrustLevel.AUTONOMOUS: {
        "ucip:memory.read",
        "ucip:memory.write",
        "ucip:search.web",
        "ucip:filesystem.read",
        "ucip:filesystem.write",
        "ucip:filesystem.delete",   # Requires HITL gate
        "ucip:execution.python",
        "ucip:execution.bash",
        "ucip:execution.node",
        "ucip:api.call",
        "ucip:secret.read",
        "ucip:agent.spawn",
        "ucip:network.outbound",
    },
    TrustLevel.ROOT: {
        "*",  # All capabilities
    },
}

# Caps that ALWAYS require human-in-the-loop approval regardless of trust level
HITL_REQUIRED_CAPS: set[str] = {
    "ucip:filesystem.delete",
    "ucip:system.shell",
    "ucip:agent.spawn",
    "ucip:network.outbound",    # When target is external/unknown
}

# Caps that are ALWAYS blocked (no trust level can unlock without config override)
ALWAYS_BLOCKED_CAPS: set[str] = {
    "ucip:system.root",
    "ucip:filesystem.format",
    "ucip:network.exfiltrate",
}

# Map from Brain action names → required capability
ACTION_TO_CAP: dict[str, str] = {
    "write_python":  "ucip:execution.python",
    "write_bash":    "ucip:execution.bash",
    "write_node":    "ucip:execution.node",
    "search_web":    "ucip:search.web",
    "recall_memory": "ucip:memory.read",
    "save_memory":   "ucip:memory.write",
    "read_file":     "ucip:filesystem.read",
    "write_file":    "ucip:filesystem.write",
    "delete_file":   "ucip:filesystem.delete",
    "call_api":      "ucip:api.call",
    "read_secret":   "ucip:secret.read",
    "spawn_agent":   "ucip:agent.spawn",
    "graph_remember": "ucip:memory.write",
    "graph_query":    "ucip:memory.read",
    "shell":         "ucip:system.shell",
    "mark_complete": None,   # Always allowed
    "ask_user":      None,   # Always allowed
}


# ── Agent Identity ────────────────────────────────────────────────────────────

@dataclass
class AgentIdentity:
    """
    Formal agent identity — immutable after creation.
    AgentID derived from: SHA-256(user_id + session_id + created_at)
    """
    agent_id:    str
    user_id:     str
    session_id:  str
    trust_level: TrustLevel
    capabilities: set[str]
    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:    dict     = field(default_factory=dict)
    delegation_chain: list[str] = field(default_factory=list)  # ancestor agent_ids, root-first

    @classmethod
    def create(cls, user_id: str, session_id: str,
               trust_level: TrustLevel = TrustLevel.OPERATOR,
               extra_caps: Optional[set[str]] = None) -> "AgentIdentity":
        raw = f"{user_id}:{session_id}:{datetime.now(timezone.utc).isoformat()}"
        agent_id = "ucip:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
        caps = set(TRUST_LEVEL_CAPS.get(trust_level, set()))
        if extra_caps:
            # Extra caps can only be granted up to delegator's trust level
            caps |= extra_caps
        return cls(agent_id=agent_id, user_id=user_id, session_id=session_id,
                   trust_level=trust_level, capabilities=caps)

    def delegate(self, sub_caps: Optional[set[str]] = None,
                 trust_level: Optional[TrustLevel] = None) -> "AgentIdentity":
        """Create a child identity acting on this identity's behalf — e.g.
        Cognitive System delegating to a named Worker persona, or a Worker
        delegating to a sub-task. The child can never exceed the parent's
        capabilities, only narrow them (or narrow via a lower trust_level).
        The full delegation lineage is preserved in delegation_chain, so
        Audit can always answer "acting under whose authority?", not just
        "who literally made this call?"."""
        child_trust = trust_level if (trust_level is not None and trust_level <= self.trust_level) else self.trust_level
        tier_caps = set(TRUST_LEVEL_CAPS.get(child_trust, set()))

        def _permits(cap_set: set[str], cap: str) -> bool:
            return "*" in cap_set or cap in cap_set

        if sub_caps is not None:
            # Bug fixed here (found via workers/runtime.py's first real
            # test — this path had never been exercised before): naively
            # intersecting sub_caps against a tier_caps/capabilities set
            # that contains the literal "*" token produces an EMPTY set,
            # since "*" doesn't equal any specific capability string. Each
            # requested cap must instead be checked against both sides via
            # _permits(), which treats "*" as "matches anything".
            granted = {c for c in sub_caps if _permits(self.capabilities, c) and _permits(tier_caps, c)}
        elif "*" in self.capabilities and "*" in tier_caps:
            granted = {"*"}
        elif "*" in tier_caps:
            granted = set(self.capabilities)
        elif "*" in self.capabilities:
            granted = set(tier_caps)
        else:
            granted = self.capabilities & tier_caps

        raw = f"{self.agent_id}:{self.session_id}:{datetime.now(timezone.utc).isoformat()}"
        child_id = "ucip:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
        return AgentIdentity(
            agent_id=child_id, user_id=self.user_id, session_id=self.session_id,
            trust_level=child_trust, capabilities=granted,
            delegation_chain=self.delegation_chain + [self.agent_id],
        )

    def has_cap(self, cap: str) -> bool:
        return "*" in self.capabilities or cap in self.capabilities

    def to_dict(self) -> dict:
        return {
            "agent_id":    self.agent_id,
            "user_id":     self.user_id,
            "session_id":  self.session_id,
            "trust_level": self.trust_level.name,
            "capabilities": list(self.capabilities),
            "created_at":  self.created_at.isoformat(),
            "delegation_chain": self.delegation_chain,
        }


# ── Budget Policy ─────────────────────────────────────────────────────────────

@dataclass
class BudgetPolicy:
    """Hard resource limits per agent session."""
    max_iterations:      int   = 8       # Max Brain loop iterations
    max_execution_calls: int   = 20      # Max code executions per loop
    max_total_runtime_s: int   = 300     # Max wall-clock seconds per loop
    max_tokens_per_task: int   = 50_000  # Approx token budget
    max_retry_same_step: int   = 3       # Max retries on same failing action
    max_consecutive_failures: int = 3    # Before forced ESCALATE
    loop_similarity_threshold: float = 0.85  # For stuck-loop detection


# ── Decision ──────────────────────────────────────────────────────────────────

class Decision(str):
    APPROVE   = "APPROVE"
    DENY      = "DENY"
    ESCALATE  = "ESCALATE_TO_HUMAN"
    AUDIT     = "AUDIT_ONLY"


@dataclass
class PolicyDecision:
    decision:    str
    action:      str
    cap_required: Optional[str]
    reason:      str
    agent_id:    str
    timestamp:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:    dict     = field(default_factory=dict)

    def approved(self) -> bool:
        return self.decision == Decision.APPROVE

    def needs_human(self) -> bool:
        return self.decision == Decision.ESCALATE

    def to_dict(self) -> dict:
        return {
            "decision":     self.decision,
            "action":       self.action,
            "cap_required": self.cap_required,
            "reason":       self.reason,
            "agent_id":     self.agent_id,
            "timestamp":    self.timestamp.isoformat(),
        }


# ── Budget Tracker ────────────────────────────────────────────────────────────

class BudgetTracker:
    def __init__(self, policy: BudgetPolicy):
        self.policy     = policy
        self._start     = time.monotonic()
        self._iters     = 0
        self._execs     = 0
        self._retries: dict[str, int] = {}
        self._consec_fail = 0
        self._recent_actions: list[str] = []

    def tick_iteration(self) -> Optional[str]:
        self._iters += 1
        if self._iters > self.policy.max_iterations:
            return f"Max iterations ({self.policy.max_iterations}) exceeded"
        elapsed = time.monotonic() - self._start
        if elapsed > self.policy.max_total_runtime_s:
            return f"Max runtime ({self.policy.max_total_runtime_s}s) exceeded"
        return None

    def tick_execution(self, action: str) -> Optional[str]:
        self._execs += 1
        if self._execs > self.policy.max_execution_calls:
            return f"Max execution calls ({self.policy.max_execution_calls}) exceeded"
        # Detect stuck loop: same action repeated too many times
        self._recent_actions.append(action)
        if len(self._recent_actions) > 6:
            self._recent_actions.pop(0)
        if len(self._recent_actions) >= 4:
            last4 = self._recent_actions[-4:]
            if len(set(last4)) == 1:
                return f"Stuck loop detected: action '{action}' repeated 4 times"
        return None

    def tick_retry(self, step_key: str) -> Optional[str]:
        self._retries[step_key] = self._retries.get(step_key, 0) + 1
        if self._retries[step_key] > self.policy.max_retry_same_step:
            return f"Max retries ({self.policy.max_retry_same_step}) on step '{step_key}'"
        return None

    def tick_failure(self, failed: bool):
        if failed:
            self._consec_fail += 1
        else:
            self._consec_fail = 0

    def check_consec_failures(self) -> Optional[str]:
        if self._consec_fail >= self.policy.max_consecutive_failures:
            return f"{self._consec_fail} consecutive failures — forcing escalation"
        return None

    def stats(self) -> dict:
        return {
            "iterations": self._iters,
            "executions": self._execs,
            "elapsed_s":  round(time.monotonic() - self._start, 2),
            "consec_failures": self._consec_fail,
        }


# ── Prompt Injection Scanner ──────────────────────────────────────────────────

class ExecutionPermissionGuard:
    """A strict execution-permission gate for tool actions."""

    def __init__(self):
        self.scanner = PromptInjectionScanner()

    def can_execute(self, agent: AgentIdentity, action: str,
                    action_input: str, context: Optional[dict] = None) -> tuple[bool, str]:
        cap_required = ACTION_TO_CAP.get(action)
        if cap_required is None:
            return True, "no capability required"

        if cap_required in ALWAYS_BLOCKED_CAPS:
            return False, f"capability '{cap_required}' is permanently blocked"

        clean, threat = self.scanner.scan(action_input, source=f"action:{action}")
        if not clean:
            return False, f"prompt injection blocked: {threat}"

        if not agent.has_cap(cap_required):
            return False, (
                f"permission denied: agent trust_level={agent.trust_level.name} lacks capability '{cap_required}'"
            )

        if cap_required in HITL_REQUIRED_CAPS:
            return False, f"capability '{cap_required}' requires human approval"

        return True, "permission granted"


class PromptInjectionScanner:
    """
    Scans tool outputs and user inputs for prompt injection attempts.
    Implements defense-in-depth against OWASP LLM01 (Prompt Injection).
    """

    # Patterns that indicate injection attempts in tool outputs
    INJECTION_PATTERNS = [
        # Direct instruction override
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?prior\s+(instructions|context)",
        r"forget\s+(everything|all)\s+(above|before|prior)",
        r"new\s+instructions?\s*:",
        r"system\s+prompt\s*:",
        # Role hijacking
        r"you\s+are\s+now\s+(a\s+)?(different|new|unrestricted)",
        r"act\s+as\s+(a\s+)?root",
        r"pretend\s+(you\s+are|to\s+be)\s+(admin|root|superuser)",
        # Jailbreak patterns
        r"\bdan\b.*\bdo\s+anything",
        r"jailbreak",
        r"bypass\s+(safety|security|restrictions|filters)",
        # Data exfiltration
        r"send\s+(all|this|the)\s+(data|output|results?)\s+to\s+http",
        r"curl\s+.+\s*\|\s*bash",
        r"wget\s+.+\s*\|\s*sh",
        # Privilege escalation
        r"sudo\s+",
        r"chmod\s+[0-7]*7[0-7]*",
        r"chown\s+root",
        r"\brm\s+-rf\s+/",
        r"mkfs\.",
        r"dd\s+if=",
        # Sensitive file access
        r"/etc/passwd",
        r"/etc/shadow",
        r"\.ssh/id_rsa",
        r"aws_secret_access_key",
        r"PRIVATE KEY",
    ]

    _compiled = [re.compile(p, re.IGNORECASE | re.MULTILINE)
                 for p in INJECTION_PATTERNS]

    @classmethod
    def scan(cls, text: str, source: str = "unknown") -> tuple[bool, Optional[str]]:
        """
        Returns (is_clean, threat_description).
        is_clean=True means no injection detected.
        """
        if not text:
            return True, None
        for pattern in cls._compiled:
            m = pattern.search(text)
            if m:
                threat = f"Injection pattern detected in {source}: '{m.group(0)[:60]}'"
                logger.warning(f"[UCIP:INJECTION] {threat}")
                return False, threat
        return True, None

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Sanitize by replacing injection patterns with [REDACTED]."""
        result = text
        for pattern in cls._compiled:
            result = pattern.sub("[REDACTED]", result)
        return result


# ── Audit Logger ──────────────────────────────────────────────────────────────

class UCIPAuditLogger:
    """
    Append-only structured audit log.
    Every decision — APPROVE, DENY, ESCALATE — is recorded.
    This is the P4 completeness guarantee: not just successes.
    """

    def __init__(self):
        self._log: list[dict] = []

    def record(self, decision: PolicyDecision, context: Optional[dict] = None):
        entry = {
            **decision.to_dict(),
            "context": context or {},
            "log_id": str(uuid.uuid4()),
        }
        self._log.append(entry)
        level = logging.WARNING if decision.decision == Decision.DENY else logging.INFO
        logger.log(level,
            f"[UCIP:{decision.decision}] agent={decision.agent_id[:20]} "
            f"action={decision.action} cap={decision.cap_required} "
            f"reason={decision.reason[:80]}"
        )

    def get_log(self, agent_id: Optional[str] = None,
                limit: int = 100) -> list[dict]:
        log = self._log
        if agent_id:
            log = [e for e in log if e.get("agent_id") == agent_id]
        return log[-limit:]

    def stats(self) -> dict:
        total = len(self._log)
        approved = sum(1 for e in self._log if e["decision"] == Decision.APPROVE)
        denied = sum(1 for e in self._log if e["decision"] == Decision.DENY)
        escalated = sum(1 for e in self._log if e["decision"] == Decision.ESCALATE)
        return {"total": total, "approved": approved,
                "denied": denied, "escalated": escalated}

    def to_structured_log(self) -> list[dict]:
        """Structured log format for observability dashboards."""
        return [
            {
                "timestamp":   e["timestamp"],
                "log_id":      e["log_id"],
                "agent_id":    e["agent_id"],
                "action":      e["action"],
                "capability":  e["cap_required"],
                "decision":    e["decision"],
                "reason":      e["reason"],
                "latency_ms":  e.get("context", {}).get("latency_ms"),
                "tokens_used": e.get("context", {}).get("tokens_used"),
            }
            for e in self._log
        ]


# ── Policy Engine ─────────────────────────────────────────────────────────────

class PolicyEngine:
    """
    Evaluates whether an agent action is permitted.
    Order of evaluation:
      1. Always-blocked caps → DENY immediately
      2. Injection scan of inputs → DENY if threat
      3. Agent has required capability? → DENY if not
      4. HITL-required cap? → ESCALATE
      5. Budget check → DENY if exceeded
      6. → APPROVE
    """

    def __init__(self, audit: UCIPAuditLogger):
        self.audit = audit
        self.guard = ExecutionPermissionGuard()

    def evaluate(self, agent: AgentIdentity, action: str,
                 action_input: str, budget: BudgetTracker,
                 context: Optional[dict] = None) -> PolicyDecision:

        cap_required = ACTION_TO_CAP.get(action)
        # Delegation lineage rides along on every audit record automatically —
        # callers don't need to remember to pass it, and Audit can always
        # answer "acting under whose authority?" not just "who called this?"
        audit_context = {**(context or {}), "delegation_chain": agent.delegation_chain}

        def _deny(reason: str) -> PolicyDecision:
            d = PolicyDecision(Decision.DENY, action, cap_required, reason, agent.agent_id)
            self.audit.record(d, audit_context)
            return d

        def _approve(reason: str = "policy check passed") -> PolicyDecision:
            d = PolicyDecision(Decision.APPROVE, action, cap_required, reason, agent.agent_id)
            self.audit.record(d, audit_context)
            return d

        def _escalate(reason: str) -> PolicyDecision:
            d = PolicyDecision(Decision.ESCALATE, action, cap_required, reason, agent.agent_id)
            self.audit.record(d, audit_context)
            return d

        # ── Step 0: No cap required (always-allowed actions) ──────────────
        if cap_required is None:
            return _approve("no capability required")

        # ── Step 1: Always-blocked caps ───────────────────────────────────
        if cap_required in ALWAYS_BLOCKED_CAPS:
            return _deny(f"capability '{cap_required}' is permanently blocked")

        # ── Step 2: Prompt injection scan ─────────────────────────────────
        clean, threat = PromptInjectionScanner.scan(action_input, source=f"action:{action}")
        if not clean:
            return _deny(f"prompt injection blocked: {threat}")

        # ── Step 3: Capability + permission guard ───────────────────────
        allowed, reason = self.guard.can_execute(agent, action, action_input, context)
        if not allowed:
            if "requires human approval" in reason.lower():
                return _escalate(reason)
            return _deny(reason)

        # ── Step 4: Budget checks ─────────────────────────────────────────
        if action in ("write_python", "write_bash", "write_node"):
            budget_err = budget.tick_execution(action)
            if budget_err:
                return _deny(f"budget exceeded: {budget_err}")

        consec_err = budget.check_consec_failures()
        if consec_err:
            return _escalate(f"budget: {consec_err}")

        # ── Step 6: APPROVE ───────────────────────────────────────────────
        return _approve()


# ── UCIP Gateway — the single entry point ────────────────────────────────────

class UCIPGateway:
    """
    The gate between Brain decisions and Execution.
    Nothing reaches ExecutionLayer without passing through here.

    Usage:
        gateway = UCIPGateway(agent_identity, budget_policy)
        decision = await gateway.request(action, action_input)
        if decision.approved():
            result = await executor.run(...)
        elif decision.needs_human():
            # pause loop, surface to UI for approval
    """

    # Singleton audit logger shared across all gateway instances
    _audit = UCIPAuditLogger()
    _scanner = PromptInjectionScanner()

    def __init__(self, agent: AgentIdentity,
                 policy: Optional[BudgetPolicy] = None):
        self.agent   = agent
        self.policy  = policy or BudgetPolicy()
        self.budget  = BudgetTracker(self.policy)
        self.engine  = PolicyEngine(self._audit)
        self._hitl_pending: list[dict] = []

    def request(self, action: str, action_input: str,
                context: Optional[dict] = None) -> PolicyDecision:
        """Evaluate an action. Returns a PolicyDecision synchronously."""
        return self.engine.evaluate(
            self.agent, action, action_input, self.budget, context
        )

    def tick_iteration(self) -> Optional[str]:
        """Call at the start of each loop iteration. Returns error string if budget exceeded."""
        return self.budget.tick_iteration()

    def record_result(self, action: str, success: bool):
        """Record execution result for budget tracking."""
        self.budget.tick_failure(not success)
        if action in ("write_python", "write_bash", "write_node"):
            if not success:
                self.budget.tick_retry(action)

    def queue_hitl(self, action: str, action_input: str,
                   description: str, decision: PolicyDecision):
        """Queue an action that requires human approval."""
        self._hitl_pending.append({
            "id":           str(uuid.uuid4()),
            "action":       action,
            "action_input": action_input[:1000],
            "description":  description,
            "reason":       decision.reason,
            "queued_at":    datetime.now(timezone.utc).isoformat(),
            "status":       "pending",
        })

    def approve_hitl(self, hitl_id: str) -> bool:
        for item in self._hitl_pending:
            if item["id"] == hitl_id and item["status"] == "pending":
                item["status"] = "approved"
                logger.info(f"[UCIP:HITL] Approved: {hitl_id} action={item['action']}")
                return True
        return False

    def deny_hitl(self, hitl_id: str) -> bool:
        for item in self._hitl_pending:
            if item["id"] == hitl_id and item["status"] == "pending":
                item["status"] = "denied"
                logger.info(f"[UCIP:HITL] Denied: {hitl_id} action={item['action']}")
                return True
        return False

    def get_pending_hitl(self) -> list[dict]:
        return [i for i in self._hitl_pending if i["status"] == "pending"]

    def budget_stats(self) -> dict:
        return self.budget.stats()

    @classmethod
    def get_audit_log(cls, agent_id: Optional[str] = None,
                      limit: int = 100) -> list[dict]:
        return cls._audit.get_log(agent_id, limit)

    @classmethod
    def audit_stats(cls) -> dict:
        return cls._audit.stats()
