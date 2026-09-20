"""Integrate SSH operations with DevOS EvidenceChain.

Every meaningful remote operation records an immutable evidence node.
Sensitive values are redacted. Do not fabricate success.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Optional

from governance.evidence import EvidenceChain
from governance.ssh_credentials import scrub_ssh_secrets_from_text, assert_no_secret_material

logger = logging.getLogger("devos.ssh_evidence")

# Process-local chains keyed by job/session (durable store can snapshot)
_CHAINS: dict[str, EvidenceChain] = {}


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def get_or_create_chain(chain_id: Optional[str] = None, *, actor_id: str = "") -> EvidenceChain:
    cid = chain_id or uuid.uuid4().hex
    if cid not in _CHAINS:
        _CHAINS[cid] = EvidenceChain(chain_id=cid, goal="ssh_remote")
    return _CHAINS[cid]


def clear_chains_for_tests() -> None:
    _CHAINS.clear()


def record_ssh_operation_evidence(
    *,
    chain_id: Optional[str] = None,
    actor_id: str,
    user_id: str = "",
    workspace_id: str = "",
    agent: str = "",
    host: str = "",
    connection_id: str = "",
    verified_fingerprint: str = "",
    credential_ref_id: str = "",
    capability: str = "ssh.exec",
    command: str = "",
    action: str = "ssh_remote_operation",
    duration_ms: int = 0,
    exit_status: Optional[int] = None,
    stdout: str = "",
    stderr: str = "",
    approval: str = "",
    policy_decision: str = "",
    verification_result: str = "",
    resulting_state: str = "",
    status: str = "success",  # success|failed|denied|cancelled|unknown
    predecessor_ids: Optional[list[str]] = None,
) -> dict:
    """Append one EvidenceNode and return a public summary for Nuha grounding."""
    chain = get_or_create_chain(chain_id, actor_id=actor_id)
    out_s = scrub_ssh_secrets_from_text(stdout or "")
    err_s = scrub_ssh_secrets_from_text(stderr or "")
    cmd_s = scrub_ssh_secrets_from_text(command or "")

    # Map status to EvidenceNode vocabulary
    node_status = status
    if status == "succeeded":
        node_status = "success"
    if status == "unknown":
        node_status = "failed"  # chain uses failed for non-success; metadata keeps unknown
        # Actually evidence status should reflect uncertainty
        node_status = "pending"  # incomplete observation

    meta = {
        "user_id": user_id or actor_id,
        "workspace_id": workspace_id,
        "agent": agent,
        "host": host,
        "connection_id": connection_id,
        "verified_host_fingerprint": verified_fingerprint,
        "credential_ref_id": credential_ref_id,  # id only, not material
        "capability": capability,
        "command_preview": cmd_s[:300],
        "exit_status": exit_status,
        "stdout_ref_hash": _hash(out_s) if out_s else None,
        "stderr_ref_hash": _hash(err_s) if err_s else None,
        "stdout_preview": out_s[:500],
        "stderr_preview": err_s[:300],
        "approval": approval,
        "policy_decision": policy_decision,
        "verification_result": verification_result,
        "resulting_state": resulting_state,
        "outcome_status": status,
    }
    assert_no_secret_material(meta)

    node_status = "success" if status in ("succeeded", "success") else (
        "denied" if status == "denied" else (
            "failed" if status in ("failed", "cancelled") else "pending"
        )
    )
    node = chain.add_node(
        action=action,
        actor_id=actor_id,
        predecessor_ids=list(predecessor_ids or []),
        input_hash=_hash(cmd_s),
        output_hash=_hash(out_s + "\n" + err_s),
        status=node_status,
        decision=policy_decision or "",
        latency_ms=int(duration_ms or 0),
        metadata=meta,
    )

    summary = {
        "chain_id": chain.chain_id,
        "node_id": node.node_id,
        "status": meta["outcome_status"],
        "evidence": {
            "host_fingerprint_verified": bool(verified_fingerprint),
            "fingerprint": verified_fingerprint,
            "command_exit_status": exit_status,
            "policy_decision": policy_decision,
            "verification_result": verification_result,
            "resulting_state": resulting_state,
            "capability": capability,
        },
        "grounded_claim": _grounded_claim(status, verification_result, exit_status),
    }
    assert_no_secret_material(summary)
    return summary


def _grounded_claim(status: str, verification: str, exit_status: Optional[int]) -> str:
    """Nuha must not claim success without evidence."""
    if status in ("failed", "denied", "cancelled"):
        return f"Operation did not succeed (status={status})."
    if status == "unknown":
        return "Outcome unknown — connection lost before completion could be confirmed. Do not assume success."
    if verification and verification.lower() in ("passed", "verified", "active", "healthy"):
        return f"Operation completed and verification passed ({verification})."
    if exit_status == 0 and not verification:
        return "Remote command exited 0; verification not yet recorded."
    if exit_status == 0 and verification:
        return f"Deployment/command completed with exit 0; verification: {verification}."
    return f"Recorded status={status} exit={exit_status}."
