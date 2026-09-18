"""
Durable resume: load plan, reconcile ephemeral runtime, resume incomplete nodes.

Rules:
- completed/verified nodes are not re-run
- external side effects not blindly repeated without evidence check
- dead processes → STALE then recoverable retry when semantics allow
- when a durable ExecutionOperation is linked, consult it before resetting
  a node to runnable/pending (operation-aware resume)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("devos.resume")

COMPLETED = {"completed", "verified", "cancelled"}
# Statuses that previously were blindly reset to pending on resume
STALE_RUNNING = {"running", "queued", "ready"}
# DAG recovery mid-states kept unless operation says otherwise
RECOVERY_STATES = {"recovering", "replanning"}
INVESTIGATE = "pending_review"  # smallest existing non-auto-exec state


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


async def resolve_linked_operation(node) -> Optional[dict]:
    """Resolve durable ExecutionOperation for a DAG node without inventing links.

    Order:
      1. node.operation_id → load_operation
      2. node.job_or_task_id as operation id → load_operation
      3. node.job_or_task_id as ExecutionJob id → job.operation_id → load_operation
      4. ExecutionOperation.task_id == job_or_task_id (agent task correlation)

    Returns None when no safe linkage exists.
    """
    from governance.execution_operations import load_operation

    op_id = getattr(node, "operation_id", None) or None
    if op_id:
        op = await load_operation(str(op_id))
        if op:
            return op

    ref = getattr(node, "job_or_task_id", None) or None
    if not ref:
        return None
    ref = str(ref)

    op = await load_operation(ref)
    if op:
        return op

    # ExecutionJob.id → operation_id
    try:
        from core.database import AsyncSessionLocal, ExecutionJob, ExecutionOperation
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            job = await db.get(ExecutionJob, ref)
            if job is not None:
                jop = getattr(job, "operation_id", None)
                if jop:
                    op = await load_operation(str(jop))
                    if op:
                        return op
                # correlation mirror
                corr = job.correlation if isinstance(job.correlation, dict) else {}
                payload = job.payload if isinstance(job.payload, dict) else {}
                for candidate in (
                    corr.get("operation_id"),
                    payload.get("operation_id"),
                ):
                    if candidate:
                        op = await load_operation(str(candidate))
                        if op:
                            return op

            # Agent task id → operation.task_id
            r = await db.execute(
                select(ExecutionOperation)
                .where(ExecutionOperation.task_id == ref)
                .order_by(ExecutionOperation.created_at.desc())
                .limit(1)
            )
            row = r.scalar_one_or_none()
            if row is not None:
                return await load_operation(row.id)
    except Exception:
        logger.debug("resolve_linked_operation db lookup failed", exc_info=True)
    return None


def _mark_investigate(node, reason: str) -> None:
    node.status = INVESTIGATE
    node.blocking_reason = reason


def _mark_completed_from_success(node, op: dict) -> None:
    """Do not fabricate verification — only complete when evidence already present."""
    ev = getattr(node, "verification_evidence", None)
    if isinstance(ev, dict) and (ev.get("ok") or ev.get("passed") or ev.get("checks")):
        node.status = "completed"
        node.blocking_reason = None
        return
    # Durable success without DAG verification evidence → do not re-dispatch
    _mark_investigate(
        node,
        f"operation_succeeded_awaiting_dag_verification:{op.get('id')}",
    )


async def _apply_operation_decision(node, op: dict, report: dict) -> bool:
    """Apply operation-aware decision. Returns True if handled (caller should not default-reset)."""
    from governance.execution_operations import (
        reconcile_operation,
        OP_SUCCEEDED,
        OP_UNKNOWN,
        OP_RUNNING,
        OP_RESERVED,
        OP_FAILED,
        OP_CANCELLED,
    )

    status = (op.get("status") or "").lower()
    oid = op.get("id")

    if status == OP_SUCCEEDED:
        _mark_completed_from_success(node, op)
        report.setdefault("op_succeeded", []).append(
            {"id": node.id, "operation_id": oid, "status": node.status}
        )
        return True

    if status == OP_UNKNOWN:
        _mark_investigate(node, f"operation_unknown:{oid}")
        report.setdefault("op_unknown", []).append(
            {"id": node.id, "operation_id": oid}
        )
        return True

    if status == OP_CANCELLED:
        node.status = "cancelled"
        node.blocking_reason = f"operation_cancelled:{oid}"
        report.setdefault("op_cancelled", []).append({"id": node.id, "operation_id": oid})
        return True

    if status == OP_FAILED:
        node.status = "failed"
        node.blocking_reason = node.blocking_reason or f"operation_failed:{oid}"
        report.setdefault("op_failed", []).append({"id": node.id, "operation_id": oid})
        return True

    if status == OP_RESERVED:
        # Side effect never started — safe to allow pending re-dispatch of *job*,
        # but node stays pending without inventing a new operation here.
        node.status = "pending"
        node.blocking_reason = None
        report.setdefault("op_reserved_reset", []).append(
            {"id": node.id, "operation_id": oid}
        )
        return True

    if status == OP_RUNNING:
        # Use existing reconcile; do not re-dispatch while RUNNING/UNKNOWN outcome
        rec = await reconcile_operation(str(oid))
        st = (rec.get("status") or status).lower()
        if st == OP_SUCCEEDED:
            _mark_completed_from_success(node, op)
            report.setdefault("op_reconciled_succeeded", []).append(
                {"id": node.id, "operation_id": oid}
            )
            return True
        if st == OP_UNKNOWN:
            _mark_investigate(node, f"operation_unknown_after_reconcile:{oid}")
            report.setdefault("op_reconciled_unknown", []).append(
                {"id": node.id, "operation_id": oid}
            )
            return True
        if st in (OP_FAILED, OP_CANCELLED):
            node.status = "failed" if st == OP_FAILED else "cancelled"
            node.blocking_reason = f"operation_{st}:{oid}"
            report.setdefault("op_reconciled_terminal", []).append(
                {"id": node.id, "operation_id": oid, "status": st}
            )
            return True
        # Still legitimately running / unresolved — leave alone
        report.setdefault("op_running_kept", []).append(
            {"id": node.id, "operation_id": oid, "status": st}
        )
        return True

    return False


async def reconcile_plan_nodes(plan) -> dict[str, Any]:
    """
    After restart: reconcile nodes with durable ExecutionOperation when linked.

    Unlinked nodes preserve prior behavior (stale running → pending, external protected).
    """
    report: dict[str, Any] = {
        "reset": [],
        "kept": [],
        "external_protected": [],
        "op_succeeded": [],
        "op_unknown": [],
        "op_failed": [],
        "op_cancelled": [],
        "op_reserved_reset": [],
        "op_reconciled_succeeded": [],
        "op_reconciled_unknown": [],
        "op_reconciled_terminal": [],
        "op_running_kept": [],
        "no_linkage": [],
    }
    for n in getattr(plan, "nodes", []) or []:
        st = (getattr(n, "status", None) or "").lower()
        ntype = (getattr(n, "type", None) or getattr(n, "kind", None) or "").lower()
        external = ntype in (
            "github_push", "github_pr", "deploy", "publish", "cloudflare_tunnel",
        ) or bool(getattr(n, "side_effect", None))

        if st in COMPLETED:
            report["kept"].append({"id": n.id, "status": st})
            continue

        # Always consult durable operation when linkage exists, for any non-terminal node
        op = await resolve_linked_operation(n)
        if op:
            handled = await _apply_operation_decision(n, op, report)
            if handled:
                continue
        else:
            if getattr(n, "job_or_task_id", None) or getattr(n, "operation_id", None):
                report["no_linkage"].append(
                    {"id": n.id, "job_or_task_id": getattr(n, "job_or_task_id", None)}
                )

        # Recovery mid-states: keep unless unlinked stale-running path below
        if st in RECOVERY_STATES:
            report["kept"].append({"id": n.id, "status": st})
            continue

        if st in STALE_RUNNING or st == "running":
            if external:
                n.status = INVESTIGATE
                report["external_protected"].append({"id": n.id, "was": st})
            else:
                n.status = "pending"
                report["reset"].append({"id": n.id, "was": st, "to": "pending"})
        else:
            report["kept"].append({"id": n.id, "status": st})
    return report


async def resume_plan(plan_id: str) -> dict[str, Any]:
    """
    Load durable plan, reconcile (operation-aware), continue mission loop if not terminal.
    """
    from brain.orchestration import get_plan_durable, OrchStatus, TERMINAL
    from brain.mission_engine import run_mission
    from brain.orchestration_store import persist_plan

    plan = await get_plan_durable(plan_id)
    if not plan:
        return {"ok": False, "error": "plan_not_found", "plan_id": plan_id}

    st = (plan.status or "").lower()
    if st in {s.value if hasattr(s, "value") else s for s in (TERMINAL if isinstance(TERMINAL, (set, list, tuple)) else [])}:
        try:
            if OrchStatus(plan.status) in TERMINAL:
                return {"ok": True, "status": plan.status, "resumed": False, "reason": "terminal"}
        except Exception:
            if st in ("cancelled", "completed", "failed", "aborted"):
                return {"ok": True, "status": plan.status, "resumed": False, "reason": "terminal"}

    project_id = getattr(plan, "workspace_id", None) or "default"
    runtime_recon = reconcile_runtime_records(project_id)
    node_recon = await reconcile_plan_nodes(plan)

    if st in ("cancellation_requested", "cancelling", "cancelled"):
        from execution.cancel_cascade import cascade_cancel_plan
        ev = await cascade_cancel_plan(plan)
        await persist_plan(plan)
        return {"ok": True, "status": "cancelled", "resumed": False, "cancel_evidence": ev,
                "runtime_reconcile": runtime_recon, "node_reconcile": node_recon}

    from execution.cancel_cascade import clear_delivery_cancel
    clear_delivery_cancel(plan_id)

    plan.status = "running"
    plan.emit("orchestration.resumed", {
        "runtime_reconcile": runtime_recon,
        "node_reconcile": node_recon,
    })
    await persist_plan(plan)

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
