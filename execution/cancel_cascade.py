"""Governed delivery cancellation cascade.

Cancellation is idempotent and best-effort:
- marks the delivery/plan cancelled
- cancels active DAG nodes
- requests cancellation of agent tasks
- records an evidence payload for callers/audit
- never fabricates successful execution evidence
"""
from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_CANCELLED: set[str] = set()


def bind_delivery(delivery_id: str, **meta: Any) -> None:
    """Bind delivery metadata.

    Metadata is intentionally not treated as durable authority here.
    Durable plan/runtime state remains the source of truth.
    """
    return None


def request_delivery_cancel(delivery_id: str) -> None:
    with _LOCK:
        _CANCELLED.add(delivery_id)


def is_delivery_cancelled(delivery_id: str) -> bool:
    with _LOCK:
        return delivery_id in _CANCELLED


def clear_delivery_cancel(delivery_id: str) -> None:
    with _LOCK:
        _CANCELLED.discard(delivery_id)


async def cascade_cancel_plan(plan) -> dict[str, Any]:
    """Cancel a plan and its cancellable in-process work.

    Idempotent: calling this more than once leaves the plan cancelled
    and does not duplicate destructive side effects.
    """
    plan_id = str(getattr(plan, "id", plan))

    request_delivery_cancel(plan_id)

    cancelled_nodes: list[str] = []
    already_terminal: list[str] = []
    task_ids: list[str] = []

    for node in getattr(plan, "nodes", []) or []:
        status = (getattr(node, "status", None) or "").lower()

        if status in {
            "completed",
            "verified",
            "cancelled",
            "failed",
        }:
            already_terminal.append(str(getattr(node, "id", "")))
            continue

        if status in {"running", "queued", "ready", "pending"}:
            node.status = "cancelled"
            if hasattr(node, "blocking_reason"):
                node.blocking_reason = "plan_cancelled"
            cancelled_nodes.append(str(getattr(node, "id", "")))

    for task_id in list(getattr(plan, "agent_task_ids", []) or []):
        task_ids.append(str(task_id))
        try:
            from brain.agent_runtime import request_cancel

            request_cancel(task_id)
        except Exception:
            # Cancellation remains represented by durable plan/node state.
            # A missing/failed in-process task cancellation must not turn
            # cancellation into a false success.
            pass

    try:
        plan.status = "cancelled"
    except Exception:
        pass

    evidence: dict[str, Any] = {
        "status": "cancelled",
        "plan_id": plan_id,
        "cancelled_nodes": cancelled_nodes,
        "already_terminal_nodes": already_terminal,
        "agent_task_ids": task_ids,
    }

    emit = getattr(plan, "emit", None)
    if callable(emit):
        emit("orchestration.cancelled", evidence)

    return evidence
