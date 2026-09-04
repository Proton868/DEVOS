"""Map web crawl failures into existing NuhaDecision vocabulary.

Does NOT implement a second recovery engine. Builds ExecutionEvidence and
calls mission_engine.decide_recovery. Any RETRY/REPLAN requires fresh UCIP.
"""
from __future__ import annotations

from typing import Any, Optional

from brain.mission_engine import (
    ExecutionEvidence,
    NuhaDecision,
    DecisionType,
    FailureClass,
    decide_recovery,
)


def crawl_to_evidence(
    *,
    node_id: str,
    crawl: dict,
    persona_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    authorization_decision: Optional[str] = None,
) -> ExecutionEvidence:
    st = (crawl or {}).get("status") or "FAILED"
    err = (crawl or {}).get("error") or ""
    success = st in ("COMPLETED",)
    # PARTIAL is not binary success for verification; mark failed for recovery choice
    if st == "PARTIAL":
        success = False
        err = err or "PARTIAL_CRAWL"
    if st == "CANCELLED":
        return ExecutionEvidence(
            node_id=node_id,
            task_id=crawl.get("crawl_id"),
            persona_id=persona_id,
            workspace_id=workspace_id,
            status="cancelled",
            success=False,
            error=err or "cancelled",
            cancelled=True,
            authorization_decision=authorization_decision,
            raw_summary=f"crawl {st}",
        )
    return ExecutionEvidence(
        node_id=node_id,
        task_id=crawl.get("crawl_id"),
        persona_id=persona_id,
        workspace_id=workspace_id,
        status="succeeded" if success else "failed",
        success=success,
        error=None if success else (err or st),
        cancelled=False,
        authorization_decision=authorization_decision,
        raw_summary=f"crawl {st} stats={crawl.get('stats_json')}",
        verification={"passed": success, "crawl_status": st},
    )




def decide_from_crawl(
    *,
    node_id: str,
    crawl: dict,
    attempt_count: int = 0,
    max_attempts: int = 2,
    persona_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    authorization_decision: Optional[str] = None,
) -> tuple[ExecutionEvidence, NuhaDecision]:
    # Enrich error strings so classify_failure maps SSRF/robots/auth correctly
    crawl = dict(crawl or {})
    err = (crawl.get("error") or "").lower()
    st = (crawl.get("status") or "").upper()
    if "blocked" in err or "ssrf" in err or "private" in err:
        crawl["error"] = f"authorization denied network_policy: {crawl.get('error')}"
        # Use ABORT path via AUTHORIZATION_FAILURE / treat as fatal in decide
    if "401" in err or "403" in err or "credential" in err or "auth required" in err:
        crawl["error"] = f"missing credential: {crawl.get('error')}"
    if "robots" in err or "disallow" in err:
        crawl["error"] = f"execution error robots: {crawl.get('error')}"
    if st == "PARTIAL" or "budget" in err or "PAGE_BUDGET" in (crawl.get("error") or ""):
        crawl["error"] = f"budget exceeded: {crawl.get('error') or 'PARTIAL'}"

    evidence = crawl_to_evidence(
        node_id=node_id,
        crawl=crawl,
        persona_id=persona_id,
        workspace_id=workspace_id,
        authorization_decision=authorization_decision,
    )
    # Fatal network policy → force ABORT before generic decide
    if "network_policy" in (evidence.error or "").lower() or "ssrf" in (evidence.error or "").lower():
        decision = NuhaDecision(
            decision_type=DecisionType.ABORT.value,
            reason="network policy / SSRF — do not retry",
            evidence_refs=[node_id, crawl.get("crawl_id") or ""],
            failure_class=FailureClass.AUTHORIZATION_FAILURE.value,
            risk_class="critical",
            requires_user=False,
        )
        return evidence, decision

    decision = decide_recovery(evidence, attempt_count=attempt_count, max_attempts=max_attempts)
    return evidence, decision

def diagnose_crawl_outcome(crawl: dict) -> dict[str, Any]:
    """Lightweight map for diagnostics / tests."""
    ev, decision = decide_from_crawl(node_id="web_crawl", crawl=crawl)
    return {
        "crawl_id": crawl.get("crawl_id"),
        "crawl_status": crawl.get("status"),
        "decision": decision.decision_type,
        "failure_class": decision.failure_class,
        "reason": decision.reason,
        "requires_user": decision.requires_user,
        "note": "Diagnosis only — Mission Engine must re-authorize any RETRY/REPLAN node via UCIP",
    }



def reauthorize_recovery_action(
    *,
    user_id: str,
    action: str = "search_web",
    action_input: str = "",
) -> tuple[bool, str]:
    """Fresh UCIP evaluation for recovery — never reuse prior authorization."""
    try:
        from governance.ucip import UCIPGateway, AgentIdentity, TrustLevel
        import uuid
        agent = AgentIdentity(
            agent_id=f"ucip:web-recovery:{uuid.uuid4().hex[:10]}",
            user_id=user_id,
            session_id=uuid.uuid4().hex[:8],
            trust_level=TrustLevel.ASSISTANT,
            capabilities={"ucip:search.web", "ucip:api.call"},
        )
        decision = UCIPGateway(agent).request(
            action,
            action_input=(action_input or "")[:500],
            context={"web_intelligence": True, "recovery": True},
        )
        ok = bool(decision.approved())
        return ok, getattr(decision, "reason", "") or ("allow" if ok else "deny")
    except Exception as e:
        return False, f"UCIP unavailable: {type(e).__name__}"


def closed_loop_crawl_recovery(
    *,
    user_id: str,
    node_id: str,
    first_crawl: dict,
    retry_crawl_factory=None,
    force_ucip_deny: bool = False,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """
    Deterministic recovery loop for tests and Mission integration.

    first_crawl: durable crawl dict from failed/partial attempt
    retry_crawl_factory: optional callable() -> crawl dict for second attempt
    """
    evidence, decision = decide_from_crawl(
        node_id=node_id,
        crawl=first_crawl,
        attempt_count=0,
        max_attempts=max_attempts,
        persona_id="research",
        workspace_id="default",
        authorization_decision="allow",
    )
    out = {
        "first_evidence": evidence.to_dict(),
        "decision": decision.to_dict(),
        "retry_executed": False,
        "retry_authorized": None,
        "second_crawl": None,
        "second_evidence": None,
        "terminal": decision.decision_type,
        "attempts": 1,
    }

    if decision.decision_type == DecisionType.ASK_USER.value:
        out["terminal"] = "waiting_for_user"
        out["worker_executed_after_decision"] = False
        return out

    if decision.decision_type == DecisionType.ABORT.value:
        out["terminal"] = "aborted"
        out["worker_executed_after_decision"] = False
        return out

    if decision.decision_type in (
        DecisionType.RETRY.value,
        DecisionType.REPAIR.value,
        DecisionType.REPLAN.value,
    ):
        if force_ucip_deny:
            ok, reason = False, "forced_deny"
        else:
            ok, reason = reauthorize_recovery_action(
                user_id=user_id,
                action_input=first_crawl.get("root_url") or first_crawl.get("normalized_root_url") or "",
            )
        out["retry_authorized"] = ok
        out["ucip_reason"] = reason
        if not ok:
            out["terminal"] = "blocked_by_ucip"
            out["worker_executed_after_decision"] = False
            return out
        if retry_crawl_factory is None:
            out["terminal"] = "authorized_awaiting_execution"
            return out
        second = retry_crawl_factory()
        out["retry_executed"] = True
        out["worker_executed_after_decision"] = True
        out["second_crawl"] = second
        out["attempts"] = 2
        ev2, dec2 = decide_from_crawl(
            node_id=node_id,
            crawl=second,
            attempt_count=1,
            max_attempts=max_attempts,
            authorization_decision="allow",
        )
        out["second_evidence"] = ev2.to_dict()
        out["second_decision"] = dec2.to_dict()
        if second.get("status") == "COMPLETED":
            out["terminal"] = "completed"
        elif second.get("status") == "PARTIAL":
            out["terminal"] = "partial_complete"
        else:
            out["terminal"] = dec2.decision_type
        return out

    out["terminal"] = decision.decision_type
    return out
