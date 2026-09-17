"""
Coding-mission progress snapshots for existing SSE/event channels.

Does not create a second event system — shapes payloads that chat/agent
SSE already stream. Never marks success before backend acceptance.
Never includes secrets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _scrub(s: str, n: int = 400) -> str:
    t = (s or "")[:n]
    for pat in ("sk-", "ghp_", "Bearer ", "SUPABASE_", "password=", "api_key="):
        if pat.lower() in t.lower():
            t = "[redacted]"
            break
    return t


def build_coding_progress(
    *,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    status: Optional[str] = None,
    agent_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    current_task: Optional[str] = None,
    files_changed: Optional[list] = None,
    command: Optional[str] = None,
    command_exit_code: Optional[int] = None,
    command_ok: Optional[bool] = None,
    command_stdout_tail: Optional[str] = None,
    command_stderr_tail: Optional[str] = None,
    check_kind: Optional[str] = None,  # test | build | lint
    check_status: Optional[str] = None,  # running | passed | failed
    provider: Optional[str] = None,
    model: Optional[str] = None,
    retry_count: Optional[int] = None,
    fallback_provider: Optional[str] = None,
    validation: Optional[dict] = None,
    artifacts: Optional[list] = None,
    error: Optional[str] = None,
    acceptance: Optional[dict] = None,
    evidence_id: Optional[str] = None,
    isolation_evidence: Optional[dict] = None,
) -> dict[str, Any]:
    """Structured coding progress for SSE. Truthful only — no success invention."""
    files: list[str] = []
    for f in files_changed or []:
        if isinstance(f, dict):
            p = f.get("path") or f.get("file")
            if p:
                files.append(str(p))
        elif isinstance(f, str) and f.strip():
            files.append(f.strip())

    acc = acceptance
    if isinstance(acc, dict) and acc.get("ok") is True and not acc.get("reason"):
        # require explicit reason from evaluate_mission_acceptance
        acc = {**acc, "ok": False, "reason": "acceptance_incomplete"}

    snap = {
        "mission_id": mission_id,
        "task_id": task_id,
        "plan_id": plan_id,
        "project_id": project_id,
        "workspace_id": workspace_id or "default",
        "status": status,
        "agent_id": agent_id,
        "persona_id": persona_id,
        "current_task": (current_task or "")[:300] or None,
        "files_changed": files[:50],
        "command": _scrub(command or "", 300) if command else None,
        "command_exit_code": command_exit_code,
        "command_ok": command_ok,
        "command_stdout_tail": _scrub(command_stdout_tail or "", 500) if command_stdout_tail else None,
        "command_stderr_tail": _scrub(command_stderr_tail or "", 500) if command_stderr_tail else None,
        "check_kind": check_kind,
        "check_status": check_status,
        "provider": provider,
        "model": model,
        "retry_count": retry_count,
        "fallback_provider": fallback_provider,
        "validation": _public_validation(validation),
        "artifacts": [str(a) for a in (artifacts or []) if a][:30],
        "error": _scrub(error or "", 400) if error else None,
        "acceptance": _public_acceptance(acc),
        "evidence_id": evidence_id,
        "isolation_evidence": isolation_evidence if isinstance(isolation_evidence, dict) else None,
        "at": datetime.now(timezone.utc).isoformat(),
        # UI must not treat presence of this object as success.
        # Final success only when authoritative acceptance.ok AND lifecycle completed.
        "success_implied": False,
        "final_success": bool(
            isinstance(acc, dict)
            and acc.get("ok") is True
            and str(status or "").lower() in ("completed", "accepted")
        ),
        "authority": "mission_checkpoint+acceptance",
        "projection": True,
    }
    return {k: v for k, v in snap.items() if v is not None}


def _public_validation(v: Optional[dict]) -> Optional[dict]:
    if not isinstance(v, dict):
        return None
    return {
        "ok": bool(v.get("ok")),
        "structure_ok": v.get("structure_ok"),
        "works": bool(v.get("works")),
        "message": _scrub(str(v.get("message") or ""), 200) or None,
    }


def _public_acceptance(a: Optional[dict]) -> Optional[dict]:
    if not isinstance(a, dict):
        return None
    return {
        "ok": bool(a.get("ok")),
        "reason": str(a.get("reason") or "")[:120],
        "status": a.get("status"),
    }


def merge_progress(base: Optional[dict], update: dict) -> dict:
    """Accumulate coding progress across SSE events (durable snapshot)."""
    out = dict(base or {})
    for k, v in (update or {}).items():
        if v is None:
            continue
        if k == "files_changed" and isinstance(v, list):
            prev = list(out.get("files_changed") or [])
            for f in v:
                if f not in prev:
                    prev.append(f)
            out["files_changed"] = prev[:50]
        elif k == "artifacts" and isinstance(v, list):
            prev = list(out.get("artifacts") or [])
            for a in v:
                if a not in prev:
                    prev.append(a)
            out["artifacts"] = prev[:30]
        else:
            out[k] = v
    out["success_implied"] = False
    if out.get("status") in ("failed", "cancelled"):
        out["acceptance"] = out.get("acceptance") or {"ok": False, "reason": out.get("status")}
    return out
