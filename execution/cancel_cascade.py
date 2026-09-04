"""
Cancellation cascade: Mission → Agent tasks → Application Runtime → Cloudflare Tunnel.

Idempotent. Does not roll back completed external side effects; records evidence only.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("devos.cancel")

# plan_id -> {user_id, project_id, runtime_ids, tunnel_ids, cancelled}
_DELIVERY_BINDINGS: dict[str, dict[str, Any]] = {}
_CANCEL_FLAGS: dict[str, bool] = {}


def bind_delivery(plan_id: str, *, user_id: str, project_id: str,
                  runtime_id: Optional[str] = None, tunnel_id: Optional[str] = None) -> None:
    b = _DELIVERY_BINDINGS.setdefault(plan_id, {
        "user_id": user_id,
        "project_id": project_id,
        "runtime_ids": set(),
        "tunnel_ids": set(),
        "cancelled": False,
    })
    b["user_id"] = user_id
    b["project_id"] = project_id
    if runtime_id:
        b["runtime_ids"].add(runtime_id)
    if tunnel_id:
        b["tunnel_ids"].add(tunnel_id)


def request_delivery_cancel(plan_id: str) -> None:
    _CANCEL_FLAGS[plan_id] = True
    if plan_id in _DELIVERY_BINDINGS:
        _DELIVERY_BINDINGS[plan_id]["cancelled"] = True


def is_delivery_cancelled(plan_id: str) -> bool:
    return bool(_CANCEL_FLAGS.get(plan_id))


def clear_delivery_cancel(plan_id: str) -> None:
    _CANCEL_FLAGS.pop(plan_id, None)


async def cascade_cancel_plan(plan) -> dict[str, Any]:
    """
    Full cascade for an OrchestrationPlan-like object.
    Safe to call multiple times.
    """
    evidence: dict[str, Any] = {
        "plan_id": getattr(plan, "id", None),
        "agent_tasks": [],
        "nodes": [],
        "runtimes": [],
        "tunnels": [],
        "already_terminal": False,
    }
    plan_id = getattr(plan, "id", None) or ""
    request_delivery_cancel(plan_id)

    # 1) Mark nodes that have not completed
    terminal_node = {"completed", "verified", "cancelled", "failed"}
    for n in getattr(plan, "nodes", []) or []:
        st = (getattr(n, "status", None) or "").lower()
        if st in ("running", "queued", "ready", "pending", "authorized", "verifying"):
            n.status = "cancelled"
            evidence["nodes"].append({"id": getattr(n, "id", None), "from": st, "to": "cancelled"})
        elif st in terminal_node:
            evidence["nodes"].append({"id": getattr(n, "id", None), "status": st, "left": True})

    # 2) Cancel agent tasks
    for tid in list(getattr(plan, "agent_task_ids", None) or []):
        try:
            from brain.agent_runtime import request_cancel as agent_cancel
            ok = agent_cancel(tid)
            evidence["agent_tasks"].append({"task_id": tid, "cancelled": bool(ok)})
        except Exception as e:
            evidence["agent_tasks"].append({"task_id": tid, "error": str(e)[:120]})

    # 3) Stop Application Runtime for bound project
    binding = _DELIVERY_BINDINGS.get(plan_id) or {}
    user_id = binding.get("user_id") or getattr(plan, "user_id", None)
    project_id = binding.get("project_id") or getattr(plan, "workspace_id", None)
    if user_id and project_id:
        try:
            from execution.app_runtime import get_runtime
            rt = get_runtime(user_id, project_id)
            if rt:
                await rt.stop()
                evidence["runtimes"].append({
                    "runtime_id": getattr(rt, "runtime_id", None),
                    "stopped": True,
                })
                try:
                    from execution.durable_store import upsert_runtime
                    import time
                    upsert_runtime({
                        "runtime_id": getattr(rt, "runtime_id", "unknown"),
                        "user_id": user_id,
                        "project_id": project_id,
                        "status": "STOPPED",
                        "stopped_at": time.time(),
                    })
                except Exception:
                    pass
        except Exception as e:
            evidence["runtimes"].append({"error": str(e)[:120]})

    # 4) Stop tunnels
    tunnel_ids = set(binding.get("tunnel_ids") or [])
    # also scan durable tunnels for project
    if project_id:
        try:
            from execution.durable_store import _conn, _LOCK
            with _LOCK:
                c = _conn()
                try:
                    rows = c.execute(
                        "SELECT tunnel_id FROM tunnels WHERE project_id=? AND status IN ('RUNNING','STARTING','READY')",
                        (project_id,),
                    ).fetchall()
                    for r in rows:
                        tunnel_ids.add(r["tunnel_id"])
                finally:
                    c.close()
        except Exception:
            pass
    for tid in tunnel_ids:
        try:
            from execution.cloudflare_tunnel_mgr import stop_tunnel
            await stop_tunnel(tid)
            evidence["tunnels"].append({"tunnel_id": tid, "stopped": True})
        except Exception as e:
            evidence["tunnels"].append({"tunnel_id": tid, "error": str(e)[:120]})

    # 5) Plan status
    try:
        plan.status = "cancelled"
        if hasattr(plan, "emit"):
            plan.emit("orchestration.cancelled", evidence)
    except Exception:
        pass

    return evidence
