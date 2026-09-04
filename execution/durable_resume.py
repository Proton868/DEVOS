"""
Durable resume: load plan, reconcile ephemeral runtime, resume incomplete nodes.

Rules:
- completed/verified nodes are not re-run
- external side effects not blindly repeated without evidence check
- dead processes → STALE then recoverable retry when semantics allow
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("devos.resume")

COMPLETED = {"completed", "verified", "cancelled"}
STALE_RUNNING = {"running", "queued", "ready"}


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def reconcile_runtime_records(project_id: str) -> list[dict]:
    """Mark durable runtimes STALE/STOPPED if PID is gone."""
    from execution.durable_store import list_runtimes, upsert_runtime
    out = []
    for row in list_runtimes(project_id):
        st = (row.get("status") or "").upper()
        pid = row.get("pid")
        if st in ("READY", "STARTING", "RUNNING", "BUILDING") and not _pid_alive(pid):
            row["status"] = "STALE"
            row["stopped_at"] = time.time()
            row["last_error"] = "process not found after restart"
            upsert_runtime(row)
            out.append({"runtime_id": row["runtime_id"], "status": "STALE"})
        else:
            out.append({"runtime_id": row["runtime_id"], "status": st, "alive": _pid_alive(pid)})
    return out


def reconcile_plan_nodes(plan) -> dict[str, Any]:
    """
    After restart: nodes stuck in RUNNING without live execution become PENDING for safe retry
    (except external side-effect nodes that already have durable success evidence).
    """
    report = {"reset": [], "kept": [], "external_protected": []}
    for n in getattr(plan, "nodes", []) or []:
        st = (getattr(n, "status", None) or "").lower()
        ntype = (getattr(n, "type", None) or getattr(n, "kind", None) or "").lower()
        external = ntype in (
            "github_push", "github_pr", "deploy", "publish", "cloudflare_tunnel",
        ) or bool(getattr(n, "side_effect", None))
        if st in COMPLETED:
            report["kept"].append({"id": n.id, "status": st})
            continue
        if st in STALE_RUNNING or st == "running":
            if external:
                # Do not auto-retry external without checking evidence
                n.status = "pending_review"
                report["external_protected"].append({"id": n.id, "was": st})
            else:
                n.status = "pending"
                report["reset"].append({"id": n.id, "was": st, "to": "pending"})
        else:
            report["kept"].append({"id": n.id, "status": st})
    return report


async def resume_plan(plan_id: str) -> dict[str, Any]:
    """
    Load durable plan, reconcile, continue mission loop if not terminal.
    """
    from brain.orchestration import get_plan_durable, OrchStatus, TERMINAL
    from brain.mission_engine import run_mission
    from brain.orchestration_store import persist_plan

    plan = await get_plan_durable(plan_id)
    if not plan:
        return {"ok": False, "error": "plan_not_found", "plan_id": plan_id}

    st = (plan.status or "").lower()
    if st in {s.value if hasattr(s, "value") else s for s in (TERMINAL if isinstance(TERMINAL, (set, list, tuple)) else [])}:
        # TERMINAL may be set of OrchStatus
        try:
            if OrchStatus(plan.status) in TERMINAL:
                return {"ok": True, "status": plan.status, "resumed": False, "reason": "terminal"}
        except Exception:
            if st in ("cancelled", "completed", "failed", "aborted"):
                return {"ok": True, "status": plan.status, "resumed": False, "reason": "terminal"}

    project_id = getattr(plan, "workspace_id", None) or "default"
    runtime_recon = reconcile_runtime_records(project_id)
    node_recon = reconcile_plan_nodes(plan)

    if st in ("cancellation_requested", "cancelling", "cancelled"):
        from execution.cancel_cascade import cascade_cancel_plan
        ev = await cascade_cancel_plan(plan)
        await persist_plan(plan)
        return {"ok": True, "status": "cancelled", "resumed": False, "cancel_evidence": ev,
                "runtime_reconcile": runtime_recon, "node_reconcile": node_recon}

    # Clear cancel flag if resuming actively
    from execution.cancel_cascade import clear_delivery_cancel
    clear_delivery_cancel(plan_id)

    plan.status = "running"
    plan.emit("orchestration.resumed", {
        "runtime_reconcile": runtime_recon,
        "node_reconcile": node_recon,
    })
    await persist_plan(plan)

    # Continue mission engine
    try:
        plan = await run_mission(plan)
        await persist_plan(plan)
    except Exception as e:
        logger.exception("resume run_mission failed")
        return {
            "ok": False,
            "error": str(e)[:300],
            "plan_id": plan_id,
            "runtime_reconcile": runtime_recon,
            "node_reconcile": node_recon,
        }

    return {
        "ok": True,
        "status": plan.status,
        "resumed": True,
        "plan": plan.to_dict() if hasattr(plan, "to_dict") else {"id": plan.id, "status": plan.status},
        "runtime_reconcile": runtime_recon,
        "node_reconcile": node_recon,
    }
