"""UCIP gate for Saga compensation — policy flags alone are not authority."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from execution.saga_compensation import (
    CompensationMode, CompensationOutcome, evaluate_conditions, policy_for,
)

logger = logging.getLogger("devos.compensation_ucip")


def _compensation_agent(user_id: str = "system"):
    from governance.ucip import AgentIdentity, TrustLevel
    # Operator-level identity with explicit compensation-related caps
    return AgentIdentity(
        agent_id=f"ucip:saga-comp:{uuid.uuid4().hex[:12]}",
        user_id=user_id or "system",
        session_id=f"saga-{uuid.uuid4().hex[:8]}",
        trust_level=TrustLevel.OPERATOR if hasattr(TrustLevel, "OPERATOR") else TrustLevel.ASSISTANT,
        capabilities={
            "ucip:filesystem.read",
            "ucip:filesystem.write",
            "ucip:filesystem.delete",
            "ucip:execution.node",
            "ucip:api.call",
            "ucip:vcs.write",
            "ucip:network.outbound",
        },
    )


def _action_for_compensation(comp_action: Optional[str]) -> str:
    a = (comp_action or "").upper()
    if a in ("REVOKE_SHARE", "REVOKE_PREVIEW", "STOP_RUNTIME", "STOP_TUNNEL", "DELETE_ARTIFACT"):
        return "write_file"  # reversible workspace/runtime ops map to fs write governance
    if a in ("DELETE_BRANCH", "CLOSE_PR"):
        return "call_api"
    if a in ("DEACTIVATE_DEPLOYMENT", "REVOKE_PUBLICATION"):
        return "call_api"
    return "write_file"


def authorize_compensation(
    *,
    forward_action: str,
    resource: Optional[dict] = None,
    user_id: str = "",
    context: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Policy evaluation + real UCIPGateway.request when requires_ucip / automatic execution.
    """
    policy = policy_for(forward_action)
    resource = resource or {}
    context = context or {}

    if policy.mode == CompensationMode.NONE:
        return {
            "allowed": False, "outcome": CompensationOutcome.SKIPPED.value,
            "reason": "none", "policy": policy.to_dict(), "ucip_decision": None,
        }
    if policy.mode == CompensationMode.MANUAL:
        return {
            "allowed": False, "outcome": CompensationOutcome.MANUAL_REMEDIATION.value,
            "reason": "manual_required", "policy": policy.to_dict(), "ucip_decision": None,
        }

    ok, why = evaluate_conditions(policy, resource, context)
    if not ok:
        return {
            "allowed": False, "outcome": CompensationOutcome.DENIED.value,
            "reason": why, "policy": policy.to_dict(), "ucip_decision": None,
        }

    # Always pass automatic/conditional compensation through UCIP when requires_ucip
    # or when risk is medium+ — enforcement not just annotation.
    must_ucip = policy.requires_ucip or policy.mode == CompensationMode.CONDITIONAL
    ucip_decision = None
    if must_ucip or policy.mode == CompensationMode.AUTOMATIC:
        try:
            from governance.ucip import UCIPGateway
            agent = _compensation_agent(user_id)
            gw = UCIPGateway(agent)
            action = _action_for_compensation(policy.action)
            decision = gw.request(
                action,
                action_input=f"compensate:{policy.action}:{resource.get('id', '')}",
                context={
                    "compensation": True,
                    "forward_action": forward_action,
                    "resource_type": resource.get("type"),
                    "resource_id": resource.get("id"),
                    "owned_by_saga": resource.get("owned_by_saga"),
                    "plan_id": context.get("plan_id"),
                    "saga_id": context.get("saga_id"),
                    "trace_id": context.get("trace_id"),
                },
            )
            if not decision.approved():
                needs = decision.needs_human() if hasattr(decision, "needs_human") else False
                return {
                    "allowed": False,
                    "outcome": (
                        CompensationOutcome.MANUAL_REMEDIATION.value if needs
                        else CompensationOutcome.DENIED.value
                    ),
                    "reason": getattr(decision, "reason", "ucip_denied"),
                    "policy": policy.to_dict(),
                    "ucip_decision": str(getattr(decision.decision, "value", decision.decision)),
                }
            ucip_decision = str(getattr(decision.decision, "value", "APPROVE"))
        except Exception as e:
            logger.warning("UCIP compensation gate error: %s", e)
            return {
                "allowed": False,
                "outcome": CompensationOutcome.DENIED.value,
                "reason": f"ucip_error:{type(e).__name__}",
                "policy": policy.to_dict(),
                "ucip_decision": None,
            }

    if policy.requires_hitl:
        return {
            "allowed": False,
            "outcome": CompensationOutcome.MANUAL_REMEDIATION.value,
            "reason": "hitl_required",
            "policy": policy.to_dict(),
            "ucip_decision": ucip_decision,
        }

    return {
        "allowed": True,
        "outcome": CompensationOutcome.COMPENSATED.value,
        "reason": "authorized",
        "policy": policy.to_dict(),
        "ucip_decision": ucip_decision,
    }
