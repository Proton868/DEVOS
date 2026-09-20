"""SSH command risk / governance engine.

Layered policy — NOT a guarantee of safety. Static classification is advisory.
Authorization is decided by combining risk class, actor, host context, and
explicit confirmation. Remote stdout cannot redefine policy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    MODIFYING = "modifying"
    PRIVILEGED = "privileged"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


# Confirmation required at or above this (unless documented automation policy).
REQUIRES_CONFIRMATION = {
    RiskClass.PRIVILEGED,
    RiskClass.DESTRUCTIVE,
    RiskClass.UNKNOWN,
}


@dataclass
class CommandPolicyInput:
    command: str
    actor: str = "agent"  # agent | user | automation
    working_directory: Optional[str] = None
    host_id: Optional[str] = None
    requested_risk_class: Optional[str] = None
    user_confirmed: bool = False
    automation_policy_allows: bool = False  # explicit durable policy override
    history_high_risk_count: int = 0


@dataclass
class CommandPolicyDecision:
    risk_class: RiskClass
    allowed: bool
    requires_confirmation: bool
    reasons: list[str] = field(default_factory=list)
    matched_patterns: list[str] = field(default_factory=list)
    sanitized_command: str = ""
    # Policy is never taken from remote output
    remote_output_influence: bool = False

    def to_dict(self) -> dict:
        return {
            "risk_class": self.risk_class.value,
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "reasons": list(self.reasons),
            "matched_patterns": list(self.matched_patterns),
            "sanitized_command": self.sanitized_command,
            "remote_output_influence": self.remote_output_influence,
        }


# Pattern layers (order: destructive > privileged > modifying > read)
_DESTRUCTIVE = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|--recursive.*--force|--force.*--recursive)\b", "rm_rf"),
    (r"\bmkfs(\.| )" , "mkfs"),
    (r"\bdd\s+.*\bof=/dev/", "dd_disk"),
    (r"\b(parted|fdisk|gdisk)\b", "partition_tool"),
    (r"\bdrop\s+database\b", "drop_database"),
    (r"\b(shred|wipefs)\b", "wipe"),
    (r">\s*/dev/sd[a-z]", "redirect_disk"),
    (r"\buserdel\b|\bdeluser\b", "delete_user"),
    (r"\bcrontab\s+-r\b", "crontab_wipe"),
]

_PRIVILEGED = [
    (r"\bsudo\b", "sudo"),
    (r"\bsystemctl\s+(restart|stop|disable|mask|daemon-reload)\b", "systemctl_mutate"),
    (r"\biptables\b|\bnft\b|\bufw\b", "firewall"),
    (r"\bdocker\s+(system\s+prune|network\s+|swarm\s+)", "docker_privileged"),
    (r"\bchmod\s+[0-7]*[67][0-7]{2}\b", "chmod_world"),
    (r"\bchown\b", "chown"),
    (r"\bpasswd\b|\busermod\b|\buseradd\b|\badduser\b", "user_mgmt"),
    (r"\bmount\b|\bumount\b", "mount"),
]

_MODIFYING = [
    (r"\b(apt|apt-get|yum|dnf|apk)\s+(install|remove|upgrade|dist-upgrade)\b", "package_mgr"),
    (r"\b(npm|pip|pip3|gem|cargo)\s+install\b", "lang_pkg"),
    (r"\b(mkdir|cp|mv|touch|chmod|ln)\b", "fs_mutate"),
    (r"\bgit\s+(pull|push|checkout|merge|rebase|reset)\b", "git_mutate"),
    (r"\bdocker\s+(run|build|pull|rm|stop|start)\b", "docker_mutate"),
    (r"\bsystemctl\s+(start|enable)\b", "systemctl_start"),
    (r"\b(tee|sed\s+-i|echo\s+.*>)\b", "write_redirect"),
]

_READ_ONLY = [
    (r"^\s*(ls|pwd|whoami|id|uname|hostname|df|free|uptime|date|env|printenv)\b", "basic_read"),
    (r"^\s*(cat|head|tail|less|more|wc|file|stat|du)\b", "file_read"),
    (r"^\s*(ps|top|htop|ss|netstat|ip\s+a|ip\s+addr)\b", "process_net_read"),
    (r"^\s*systemctl\s+status\b", "systemctl_status"),
    (r"^\s*docker\s+(ps|images|inspect|logs|version)\b", "docker_read"),
    (r"^\s*(journalctl|dmesg)\b", "log_read"),
    (r"^\s*git\s+(status|log|diff|show|branch)\b", "git_read"),
]


def _match_layer(command: str, patterns: list) -> list[str]:
    hits = []
    for pat, name in patterns:
        if re.search(pat, command, re.IGNORECASE):
            hits.append(name)
    return hits


def classify_command(command: str) -> tuple[RiskClass, list[str], list[str]]:
    """Advisory classification. UNKNOWN is the safe default for unmatched."""
    cmd = (command or "").strip()
    reasons: list[str] = []
    if not cmd:
        return RiskClass.UNKNOWN, ["empty_command"], []

    # Shell metacharacters that enable chaining → escalate uncertainty
    if re.search(r"[;&|`$]|\$\(|<\(", cmd):
        reasons.append("shell_metacharacters")

    dest = _match_layer(cmd, _DESTRUCTIVE)
    if dest:
        return RiskClass.DESTRUCTIVE, reasons + ["matched_destructive"], dest

    priv = _match_layer(cmd, _PRIVILEGED)
    if priv:
        return RiskClass.PRIVILEGED, reasons + ["matched_privileged"], priv

    mod = _match_layer(cmd, _MODIFYING)
    if mod:
        # Metacharacters on modifying commands → treat as unknown (injection risk)
        if "shell_metacharacters" in reasons:
            return RiskClass.UNKNOWN, reasons + ["matched_modifying_with_meta"], mod
        return RiskClass.MODIFYING, reasons + ["matched_modifying"], mod

    read = _match_layer(cmd, _READ_ONLY)
    if read and not reasons:
        return RiskClass.READ_ONLY, reasons + ["matched_read_only"], read
    if read and reasons:
        return RiskClass.UNKNOWN, reasons + ["read_with_meta"], read

    return RiskClass.UNKNOWN, reasons + ["unclassified"], []


def evaluate_command_policy(inp: CommandPolicyInput) -> CommandPolicyDecision:
    risk, reasons, matched = classify_command(inp.command)
    sanitized = (inp.command or "").strip()[:4000]

    # Caller-requested risk cannot *lower* classification
    if inp.requested_risk_class:
        try:
            req = RiskClass(inp.requested_risk_class)
            order = [
                RiskClass.READ_ONLY,
                RiskClass.LOW_RISK,
                RiskClass.MODIFYING,
                RiskClass.PRIVILEGED,
                RiskClass.DESTRUCTIVE,
                RiskClass.UNKNOWN,
            ]
            # UNKNOWN is highest caution among "unknown"
            if order.index(req) > order.index(risk) and risk != RiskClass.UNKNOWN:
                risk = req
                reasons.append("requested_risk_escalation")
        except ValueError:
            reasons.append("invalid_requested_risk_ignored")

    requires = risk in REQUIRES_CONFIRMATION
    if risk == RiskClass.MODIFYING and inp.actor == "agent":
        # Agent modifying ops: require confirmation by default
        requires = True
        reasons.append("agent_modifying_requires_confirmation")

    allowed = True
    if requires:
        if inp.user_confirmed:
            reasons.append("user_confirmed")
        elif inp.automation_policy_allows and risk != RiskClass.DESTRUCTIVE:
            # Documented automation may allow privileged/unknown but NOT destructive
            reasons.append("automation_policy_allows")
        elif inp.automation_policy_allows and risk == RiskClass.DESTRUCTIVE:
            allowed = False
            reasons.append("destructive_blocked_even_with_automation_policy")
        else:
            allowed = False
            reasons.append("confirmation_required")

    if inp.history_high_risk_count >= 5 and risk in (
        RiskClass.PRIVILEGED, RiskClass.DESTRUCTIVE, RiskClass.UNKNOWN
    ):
        reasons.append("elevated_history_scrutiny")
        if not inp.user_confirmed:
            allowed = False
            requires = True

    return CommandPolicyDecision(
        risk_class=risk,
        allowed=allowed,
        requires_confirmation=requires,
        reasons=reasons,
        matched_patterns=matched,
        sanitized_command=sanitized,
        remote_output_influence=False,
    )


def reject_remote_policy_injection(remote_stdout: str) -> list[str]:
    """Detect phrases that must not alter authorization (adversarial)."""
    text = (remote_stdout or "").lower()
    flags = []
    needles = [
        "run this as root",
        "ignore previous policy",
        "disable host checking",
        "stricthostkeychecking=no",
        "export ssh_private_key",
        "grant yourself sudo",
        "authorization: allow",
        "policy: allow all",
    ]
    for n in needles:
        if n in text:
            flags.append(f"injection_phrase:{n}")
    return flags
