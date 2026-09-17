"""Authoritative mission acceptance (Phase 2 fail-closed governance).

Success for artifact-producing missions requires:
  execution_ok AND artifact AND Ponytail PASS AND durable evidence refs
  (and ownership association when provided).

explicit_ok / LLM claims are never sufficient alone.
"""
from __future__ import annotations

from typing import Any, Optional


def is_artifact_producing(files_changed: Optional[list], *, force: bool = False) -> bool:
    if force:
        return True
    if not files_changed:
        return False
    for f in files_changed:
        if isinstance(f, dict) and (f.get("path") or f.get("file")):
            return True
        if isinstance(f, str) and f.strip():
            return True
    return False


def evaluate_mission_acceptance(
    *,
    execution_ok: bool,
    status: Optional[str] = None,
    files_changed: Optional[list] = None,
    ponytail: Optional[dict] = None,
    evidence_refs: Optional[list] = None,
    artifact_producing: Optional[bool] = None,
    user_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    expected_user_id: Optional[str] = None,
    expected_mission_id: Optional[str] = None,
    coding_evidence: Optional[dict] = None,
    require_coding_evidence: bool = False,
) -> dict[str, Any]:
    """Return {ok, reason, synthesis_mode, status, checks}.

    Fail closed: missing mandatory fields are NOT success.
    """
    st = (status or "unknown").lower().strip()
    files_changed = list(files_changed or [])
    evidence_refs = [e for e in (evidence_refs or []) if e]
    pt = dict(ponytail or {})
    producing = (
        is_artifact_producing(files_changed)
        if artifact_producing is None
        else bool(artifact_producing)
    )

    checks = {
        "execution_ok": bool(execution_ok),
        "artifact_producing": producing,
        "has_artifact": is_artifact_producing(files_changed),
        "ponytail_present": bool(pt),
        "ponytail_passed": bool(pt.get("passed")),
        "ponytail_applicable": pt.get("applicable", True) if pt else None,
        "has_evidence": bool(evidence_refs),
        "owner_match": True,
        "mission_match": True,
        "coding_evidence_ok": None,
    }

    coding_ev = coding_evidence
    if require_coding_evidence or coding_ev is not None:
        from brain.coding_evidence import validate_coding_evidence
        ce_res = validate_coding_evidence(
            coding_ev or {},
            expected_mission_id=expected_mission_id or mission_id,
            expected_user_id=expected_user_id or user_id,
            require_commands=True,
            require_files_if_success=bool(execution_ok),
        )
        checks["coding_evidence_ok"] = bool(ce_res.get("ok"))
        checks["coding_evidence_reason"] = ce_res.get("reason")
        if not ce_res.get("ok"):
            return _fail(st, f"coding_evidence:{ce_res.get('reason')}", checks)
        # Bind evidence ref from packet when refs empty
        if coding_ev and isinstance(coding_ev, dict):
            eid = coding_ev.get("evidence_id")
            if eid and eid not in evidence_refs:
                evidence_refs = list(evidence_refs) + [eid]
                checks["has_evidence"] = True
            if coding_ev.get("fabricated"):
                return _fail(st, "coding_evidence:fabricated_evidence", checks)

    if expected_user_id and user_id and expected_user_id != user_id:
        checks["owner_match"] = False
    if expected_mission_id and mission_id and expected_mission_id != mission_id:
        checks["mission_match"] = False

    if not checks["owner_match"]:
        return _fail(st, "owner_mismatch", checks)
    if not checks["mission_match"]:
        return _fail(st, "mission_mismatch", checks)

    if not execution_ok:
        return _fail(st, "execution_not_ok", checks)

    if not producing:
        # Non-artifact mission: execution_ok + non-failure status is enough
        if st in ("failed", "rejected", "denied", "cancelled", "error"):
            return _fail(st, "terminal_failure", checks)
        return {
            "ok": True,
            "reason": "non_artifact_execution_ok",
            "synthesis_mode": "success",
            "status": st or "succeeded",
            "checks": checks,
        }

    # Artifact-producing: Ponytail mandatory
    if not pt:
        return _fail(st, "ponytail_missing", checks)

    applicable = pt.get("applicable", True)
    if applicable is False:
        # Explicit N/A policy only
        if not checks["has_artifact"]:
            return _fail(st, "artifact_missing", checks)
        if not checks["has_evidence"]:
            return _fail(st, "evidence_missing", checks)
        return {
            "ok": True,
            "reason": "ponytail_not_applicable_with_evidence",
            "synthesis_mode": "success",
            "status": st or "succeeded",
            "checks": checks,
        }

    if not pt.get("passed"):
        return _fail(st, "ponytail_rejected", checks)

    if not checks["has_artifact"]:
        return _fail(st, "artifact_missing", checks)

    if not checks["has_evidence"]:
        return _fail(st, "evidence_missing", checks)

    # evidence_id claimed on gate must be non-empty string when present
    eid = pt.get("evidence_id")
    if eid is not None and not str(eid).strip():
        return _fail(st, "evidence_id_empty", checks)

    return {
        "ok": True,
        "reason": "accepted",
        "synthesis_mode": "success",
        "status": st if st not in ("unknown", "") else "accepted",
        "checks": checks,
    }


def _fail(st: str, reason: str, checks: dict) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": reason,
        "synthesis_mode": "failure" if reason != "ponytail_missing" else "incomplete",
        "status": st or "failed",
        "checks": checks,
    }
