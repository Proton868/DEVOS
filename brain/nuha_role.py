"""
Nuha executive role — distinguish conversation from governed execution.

Nuha is the orchestrator, not a silent tool-runner for chat.

Roles:
  conversation  — advice, Q&A, explanation (no tools, no mission)
  planning      — produce a plan only (no side effects)
  execution     — create plan → select specialist → delegate → monitor
  verification  — validate prior work / re-check evidence
  reporting     — summarize evidence / status (no new side effects)

Never fabricate completion. Never silently execute destructive actions.
UCIP / authorization / personas remain downstream of true execution.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional


class NuhaRole(str, Enum):
    CONVERSATION = "conversation"
    PLANNING = "planning"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    REPORTING = "reporting"


# Phrases that indicate the user wants real work done (not advice)
_EXEC_VERBS = re.compile(
    r"\b("
    r"build|create|implement|fix|add|remove|delete|refactor|migrate|"
    r"install|deploy|scaffold|generate|write|edit|patch|update|"
    r"run tests?|run the tests|execute|make me|set up|setup|"
    r"bootstrap|spin up|ship|commit|push|merge"
    r")\b",
    re.I,
)

_PLAN_CUES = re.compile(
    r"\b("
    r"plan(?:\s+out)?|outline|design a strategy|implementation plan|"
    r"how would you|what would you need|break(?:\s+it)?\s+down|"
    r"steps to|roadmap|propose an approach"
    r")\b",
    re.I,
)

_VERIFY_CUES = re.compile(
    r"\b("
    r"verify|validate|check if|did it work|re-?run tests?|"
    r"confirm (?:the )?(?:build|tests?|deploy)|acceptance|"
    r"ponytail|evidence for"
    r")\b",
    re.I,
)

_REPORT_CUES = re.compile(
    r"\b("
    r"status of|what(?:\'s| is) the status|what happened|summarize|report on|show (?:me )?(?:the )?evidence|"
    r"mission status|what did .+ do|progress on"
    r")\b",
    re.I,
)

_ADVICE_CUES = re.compile(
    r"\b("
    r"what is|what's|what are|how does|how do i|why (?:is|does|do)|"
    r"explain|describe|tell me about|difference between|"
    r"should i|can you explain|help me understand|advice|"
    r"best practice|pros and cons|when (?:should|to) use"
    r")\b",
    re.I,
)

_DESTRUCTIVE = re.compile(
    r"\b("
    r"delete|remove|drop|destroy|wipe|rm\s+-rf|truncate|force push|"
    r"reset --hard|format|purge"
    r")\b",
    re.I,
)

_TRIVIAL = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|yes|no|bye|"
    r"good morning|good night|how are you)\b",
    re.I,
)


@dataclass
class NuhaRoleDecision:
    role: NuhaRole
    should_orchestrate: bool
    should_delegate: bool
    should_execute_tools: bool
    plan_only: bool
    requires_explicit_confirm: bool
    reason: str
    destructive: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["role"] = self.role.value
        return d


def classify_nuha_role(text: str, *, explicit: Optional[str] = None) -> NuhaRoleDecision:
    """Classify user message into executive role. Fail closed toward conversation."""
    raw = (text or "").strip()
    if explicit:
        try:
            role = NuhaRole(explicit.lower())
            return _decision_for_role(role, reason=f"explicit:{role.value}", text=raw)
        except ValueError:
            pass

    if not raw or len(raw) < 2:
        return NuhaRoleDecision(
            role=NuhaRole.CONVERSATION,
            should_orchestrate=False,
            should_delegate=False,
            should_execute_tools=False,
            plan_only=False,
            requires_explicit_confirm=False,
            reason="empty_or_short",
        )

    if _TRIVIAL.match(raw) and len(raw) < 48:
        return NuhaRoleDecision(
            role=NuhaRole.CONVERSATION,
            should_orchestrate=False,
            should_delegate=False,
            should_execute_tools=False,
            plan_only=False,
            requires_explicit_confirm=False,
            reason="trivial_greeting",
        )

    destructive = bool(_DESTRUCTIVE.search(raw))
    has_exec = bool(_EXEC_VERBS.search(raw))
    has_plan = bool(_PLAN_CUES.search(raw))
    has_verify = bool(_VERIFY_CUES.search(raw))
    has_report = bool(_REPORT_CUES.search(raw))
    has_advice = bool(_ADVICE_CUES.search(raw))

    # Reporting on prior work (before advice — "what is the status" is not a definition)
    if has_report and not re.search(
        r"\b(create|implement|scaffold|deploy|bootstrap|delete)\b", raw, re.I
    ):
        return NuhaRoleDecision(
            role=NuhaRole.REPORTING,
            should_orchestrate=False,
            should_delegate=False,
            should_execute_tools=False,
            plan_only=False,
            requires_explicit_confirm=False,
            reason="status_or_evidence_request",
            destructive=destructive,
        )

    # Pure advice / definitional questions without exec verbs → conversation
    if has_advice and not has_exec and not has_verify:
        return NuhaRoleDecision(
            role=NuhaRole.CONVERSATION,
            should_orchestrate=False,
            should_delegate=False,
            should_execute_tools=False,
            plan_only=False,
            requires_explicit_confirm=False,
            reason="advice_or_explanation",
            destructive=destructive,
        )

    # Verification: prefer over generic exec when verify cues dominate
    # Allow "re-run tests" / "verify the build" as verification, not new feature work
    if has_verify and not re.search(
        r"\b(create|implement|scaffold|deploy|bootstrap)\b", raw, re.I
    ):
        return NuhaRoleDecision(
            role=NuhaRole.VERIFICATION,
            should_orchestrate=True,
            should_delegate=True,
            should_execute_tools=True,  # tests/checks via specialist — not arbitrary tools
            plan_only=False,
            requires_explicit_confirm=False,
            reason="verification_request",
            destructive=destructive,
        )

    # Plan-only: planning cues without hard execute verbs (or "plan how to build" without do it)
    if has_plan and not re.search(
        r"\b(do it|build it|implement it|execute|go ahead|run it)\b", raw, re.I
    ):
        # "plan a website" is still planning; "create a website" is execution
        if not has_exec or re.search(r"^\s*plan\b", raw, re.I):
            return NuhaRoleDecision(
                role=NuhaRole.PLANNING,
                should_orchestrate=True,
                should_delegate=False,
                should_execute_tools=False,
                plan_only=True,
                requires_explicit_confirm=False,
                reason="plan_only",
                destructive=destructive,
            )

    # Explicit execution
    if has_exec:
        return NuhaRoleDecision(
            role=NuhaRole.EXECUTION,
            should_orchestrate=True,
            should_delegate=True,
            should_execute_tools=True,
            plan_only=False,
            requires_explicit_confirm=destructive,
            reason="execution_intent",
            destructive=destructive,
        )

    # Heuristic intent classes from personas as secondary signal
    try:
        from brain.personas import classify_intent_heuristic
        classes = set(classify_intent_heuristic(raw))
        if classes & {"CREATION", "EXECUTION", "AUTOMATION", "MULTI-DOMAIN"}:
            return NuhaRoleDecision(
                role=NuhaRole.EXECUTION,
                should_orchestrate=True,
                should_delegate=True,
                should_execute_tools=True,
                plan_only=False,
                requires_explicit_confirm=destructive,
                reason="intent_class:" + ",".join(sorted(classes & {"CREATION", "EXECUTION", "AUTOMATION", "MULTI-DOMAIN"})),
                destructive=destructive,
            )
        if "RESEARCH" in classes and len(raw.split()) >= 8 and has_exec:
            return NuhaRoleDecision(
                role=NuhaRole.EXECUTION,
                should_orchestrate=True,
                should_delegate=True,
                should_execute_tools=True,
                plan_only=False,
                requires_explicit_confirm=False,
                reason="research_with_action",
            )
    except Exception:
        pass

    return NuhaRoleDecision(
        role=NuhaRole.CONVERSATION,
        should_orchestrate=False,
        should_delegate=False,
        should_execute_tools=False,
        plan_only=False,
        requires_explicit_confirm=False,
        reason="default_conversation",
        destructive=destructive,
    )


def _decision_for_role(role: NuhaRole, *, reason: str, text: str) -> NuhaRoleDecision:
    destructive = bool(_DESTRUCTIVE.search(text or ""))
    if role == NuhaRole.CONVERSATION:
        return NuhaRoleDecision(
            role=role, should_orchestrate=False, should_delegate=False,
            should_execute_tools=False, plan_only=False,
            requires_explicit_confirm=False, reason=reason, destructive=destructive,
        )
    if role == NuhaRole.PLANNING:
        return NuhaRoleDecision(
            role=role, should_orchestrate=True, should_delegate=False,
            should_execute_tools=False, plan_only=True,
            requires_explicit_confirm=False, reason=reason, destructive=destructive,
        )
    if role == NuhaRole.REPORTING:
        return NuhaRoleDecision(
            role=role, should_orchestrate=False, should_delegate=False,
            should_execute_tools=False, plan_only=False,
            requires_explicit_confirm=False, reason=reason, destructive=destructive,
        )
    if role == NuhaRole.VERIFICATION:
        return NuhaRoleDecision(
            role=role, should_orchestrate=True, should_delegate=True,
            should_execute_tools=True, plan_only=False,
            requires_explicit_confirm=False, reason=reason, destructive=destructive,
        )
    # EXECUTION
    return NuhaRoleDecision(
        role=role, should_orchestrate=True, should_delegate=True,
        should_execute_tools=True, plan_only=False,
        requires_explicit_confirm=destructive, reason=reason, destructive=destructive,
    )
