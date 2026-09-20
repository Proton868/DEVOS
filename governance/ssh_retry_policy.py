"""Retry safety for agentic SSH operations.

SAFE_TO_RETRY | CONDITIONALLY_SAFE | NOT_SAFE_TO_RETRY

Never blindly retry after disconnect on potentially consequential ops.
Prefer remote state inspection before a second attempt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RetrySafety(str, Enum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    CONDITIONALLY_SAFE = "CONDITIONALLY_SAFE"
    NOT_SAFE_TO_RETRY = "NOT_SAFE_TO_RETRY"


@dataclass
class RetryDecision:
    safety: RetrySafety
    reason: str
    inspect_first: Optional[str] = None  # diagnostic capability or command template
    allow_retry: bool = False
    remote_state_verified: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "safety": self.safety.value,
            "reason": self.reason,
            "inspect_first": self.inspect_first,
            "allow_retry": self.allow_retry,
            "remote_state_verified": self.remote_state_verified,
            "notes": list(self.notes),
        }


# Read-only patterns → safe
_SAFE = [
    re.compile(r"^\s*(ls|pwd|whoami|uname|hostname|id|date|uptime|df|free|ps|top|cat|head|tail|journalctl|systemctl\s+status|docker\s+ps|docker\s+logs|ss|netstat|ip\s+a)\b", re.I),
]

# Conditional — need state check
_CONDITIONAL = [
    (re.compile(r"\b(apt|apt-get|dnf|yum|apk)\s+install\b", re.I), "package_state", "GetSystemInfo"),
    (re.compile(r"\bsystemctl\s+(restart|reload|start|stop)\b", re.I), "service_state", "GetServiceStatus"),
    (re.compile(r"\b(docker\s+(run|start|stop|rm)|kubectl\s+apply)\b", re.I), "container_state", "ListContainers"),
    (re.compile(r"\b(scp|rsync|cp\s+.+\s+/)\b", re.I), "file_checksum", None),
    (re.compile(r"\b(npm|pip|cargo)\s+install\b", re.I), "package_state", None),
    (re.compile(r"\b(alembic|migrate|flyway)\b", re.I), "migration_state", None),
]

# Never auto-retry
_UNSAFE = [
    re.compile(r"\b(rm\s+-rf|mkfs|dd\s+if=|shred|drop\s+table|truncate\s+table)\b", re.I),
    re.compile(r"\b(userdel|passwd|chmod\s+777|chown\s+-R)\b", re.I),
    re.compile(r"\b(iptables\s+-F|ufw\s+disable)\b", re.I),
]


def classify_retry_safety(command: str) -> RetryDecision:
    cmd = (command or "").strip()
    if not cmd:
        return RetryDecision(RetrySafety.NOT_SAFE_TO_RETRY, "empty_command", allow_retry=False)

    for rx in _UNSAFE:
        if rx.search(cmd):
            return RetryDecision(
                RetrySafety.NOT_SAFE_TO_RETRY,
                "destructive_or_irreversible",
                allow_retry=False,
                notes=["never_blind_retry"],
            )

    for rx in _SAFE:
        if rx.search(cmd) and not re.search(r"[;&|`]", cmd):
            return RetryDecision(RetrySafety.SAFE_TO_RETRY, "read_only", allow_retry=True)

    for rx, reason, inspect in _CONDITIONAL:
        if rx.search(cmd):
            return RetryDecision(
                RetrySafety.CONDITIONALLY_SAFE,
                reason,
                inspect_first=inspect,
                allow_retry=False,  # until remote state verified
                notes=["inspect_remote_state_before_retry"],
            )

    # Unknown → not safe by default
    return RetryDecision(
        RetrySafety.NOT_SAFE_TO_RETRY,
        "unknown_side_effect",
        allow_retry=False,
        notes=["default_fail_closed"],
    )


@dataclass
class OperationOutcome:
    """Outcome when connection is lost mid-operation."""
    status: str  # completed | failed | unknown
    reason: str
    may_retry: bool = False
    retry_decision: Optional[RetryDecision] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "may_retry": self.may_retry,
            "retry_decision": self.retry_decision.to_dict() if self.retry_decision else None,
        }


def classify_disconnect_outcome(
    *,
    command: str,
    exit_status: Optional[int],
    observed_output: bool,
    connection_lost: bool,
) -> OperationOutcome:
    """If connection disappears after a potentially successful command, do NOT
    assume failure or blindly retry — mark unknown unless exit was observed."""
    decision = classify_retry_safety(command)

    if not connection_lost and exit_status == 0:
        return OperationOutcome("completed", "exit_zero_observed", may_retry=False, retry_decision=decision)
    if not connection_lost and exit_status not in (0, None):
        return OperationOutcome(
            "failed",
            "nonzero_exit_observed",
            may_retry=decision.allow_retry,
            retry_decision=decision,
        )
    if connection_lost and exit_status is None:
        # Critical path: unknown — may have completed on server
        return OperationOutcome(
            "unknown",
            "connection_lost_before_exit_observed",
            may_retry=False,  # must inspect first
            retry_decision=decision,
        )
    if connection_lost and observed_output and exit_status == 0:
        return OperationOutcome("completed", "exit_observed_before_disconnect", may_retry=False, retry_decision=decision)
    return OperationOutcome("unknown", "inconclusive", may_retry=False, retry_decision=decision)


async def authorize_retry_after_inspect(
    decision: RetryDecision,
    *,
    remote_state_matches_desired: bool,
    already_completed: bool,
) -> RetryDecision:
    """After remote inspection, upgrade conditional retries carefully."""
    if already_completed:
        decision.allow_retry = False
        decision.remote_state_verified = True
        decision.notes.append("already_completed_no_retry")
        return decision
    if decision.safety == RetrySafety.SAFE_TO_RETRY:
        decision.allow_retry = True
        decision.remote_state_verified = True
        return decision
    if decision.safety == RetrySafety.CONDITIONALLY_SAFE and remote_state_matches_desired is False:
        # Desired state not yet reached → conditional retry allowed once verified absent
        decision.allow_retry = True
        decision.remote_state_verified = True
        decision.notes.append("state_absent_retry_allowed")
        return decision
    if decision.safety == RetrySafety.CONDITIONALLY_SAFE and remote_state_matches_desired:
        decision.allow_retry = False
        decision.remote_state_verified = True
        decision.notes.append("desired_state_present")
        return decision
    decision.allow_retry = False
    return decision
