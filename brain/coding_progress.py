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
    usage: Optional[dict] = None,
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
        "usage": _public_usage(usage),
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



def accumulate_usage(
    existing: Optional[dict],
    last_call: Optional[dict],
    *,
    primary_provider: Optional[str] = None,
    attempt_state: Optional[dict] = None,
) -> Optional[dict]:
    """Merge one model-call metrics blob into mission-level cumulative usage.

    Token counts are summed across successful calls that report them.
    Missing fields stay absent (never fabricated as 0).
    Latency is total observed model-call time when each call reports latency_ms.
    Retry/fallback come from attempt_state when provided.
    """
    if not isinstance(last_call, dict) and not isinstance(existing, dict):
        return None
    out: dict = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(last_call, dict) and last_call:
        for k in ("provider", "model"):
            if last_call.get(k) is not None:
                out[k] = last_call[k]
        for k in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
            v = last_call.get(k)
            if v is None:
                continue
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if k in out and out[k] is not None:
                try:
                    out[k] = int(out[k]) + iv
                except (TypeError, ValueError):
                    out[k] = iv
            else:
                out[k] = iv
        lat = last_call.get("latency_ms")
        if lat is not None:
            try:
                lf = float(lat)
                out["latency_ms"] = int(round(float(out.get("latency_ms") or 0) + lf))
            except (TypeError, ValueError):
                pass
        out["call_count"] = int(out.get("call_count") or 0) + 1

    if isinstance(attempt_state, dict):
        attempts = attempt_state.get("attempts") or []
        if isinstance(attempts, list) and attempts:
            # retry_attempt = failed attempts before last success (bounded public)
            fails = sum(1 for a in attempts if isinstance(a, dict) and a.get("outcome") == "failed")
            if fails:
                out["retry_attempt"] = int(fails)
            providers = {
                a.get("provider") for a in attempts
                if isinstance(a, dict) and a.get("provider")
            }
            if primary_provider and len(providers) > 1:
                out["fallback_used"] = True
            elif primary_provider and any(
                isinstance(a, dict) and a.get("outcome") == "success"
                and a.get("provider") and a.get("provider") != primary_provider
                for a in attempts
            ):
                out["fallback_used"] = True
        if attempt_state.get("preferred_provider") and out.get("provider"):
            if attempt_state["preferred_provider"] != out.get("provider"):
                out["fallback_used"] = True

    return out or None


def _public_usage(u: Optional[dict]) -> Optional[dict]:
    """Aggregate token/latency metrics — never prompts or secrets."""
    if not isinstance(u, dict):
        return None
    out = {}
    for k in (
        "provider", "model", "input_tokens", "output_tokens", "cached_tokens",
        "total_tokens", "latency_ms", "retry_attempt", "fallback_used",
        "call_count", "mission_id", "agent_id", "persona_id",
    ):
        if u.get(k) is not None:
            out[k] = u[k]
    # UI alias: retry_count mirrors retry_attempt when only one is set
    if out.get("retry_attempt") is None and u.get("retry_count") is not None:
        out["retry_attempt"] = u["retry_count"]
    if out.get("retry_attempt") is not None:
        out.setdefault("retry_count", out["retry_attempt"])
    return out or None


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
