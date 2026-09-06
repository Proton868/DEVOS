"""Conservative recovery scan."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from core.task_contract import INCOMPLETE, TaskStatus
from core.task_registry import TaskRegistry

@dataclass
class RecoveryReport:
    incomplete: list = field(default_factory=list)
    resumable: list = field(default_factory=list)
    waiting_hitl: list = field(default_factory=list)
    waiting_child: list = field(default_factory=list)
    cancelled: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    inconsistent: list = field(default_factory=list)
    def to_dict(self):
        return {k: getattr(self, k) for k in
            ("incomplete","resumable","waiting_hitl","waiting_child","cancelled","failed","inconsistent")}

def scan_recovery(registry: Optional[TaskRegistry] = None) -> RecoveryReport:
    reg = registry or TaskRegistry()
    report = RecoveryReport()
    report.inconsistent = reg.integrity_report()
    for data in reg.list_all():
        tid = data.get("task_id"); status_s = data.get("status") or ""
        try: status = TaskStatus(status_s)
        except Exception:
            report.inconsistent.append({"task_id": tid, "issue": "invalid_status", "status": status_s}); continue
        summary = {"task_id": tid, "status": status_s, "attempt": data.get("attempt"),
            "worker_slug": data.get("worker_slug"),
            "parent_task_id": data.get("parent_task_id") or (data.get("request") or {}).get("parent_task_id"),
            "execution_id": data.get("execution_id"), "cancel_requested": bool(data.get("cancel_requested")),
            "checkpoint_reference": data.get("checkpoint_reference") or data.get("loop_id")}
        if data.get("cancel_requested") or status == TaskStatus.CANCELLED:
            report.cancelled.append(summary); continue
        if status == TaskStatus.FAILED:
            report.failed.append(summary)
            if summary["checkpoint_reference"] or data.get("request"):
                report.resumable.append({**summary, "reason": "failed_with_request"})
            continue
        if status == TaskStatus.WAITING_HITL:
            report.waiting_hitl.append(summary); report.incomplete.append(summary); continue
        if status == TaskStatus.WAITING_CHILD:
            report.waiting_child.append(summary); report.incomplete.append(summary); continue
        if status in INCOMPLETE:
            report.incomplete.append(summary)
            if not data.get("cancel_requested"):
                report.resumable.append({**summary, "reason": f"status_{status_s}"})
    return report
