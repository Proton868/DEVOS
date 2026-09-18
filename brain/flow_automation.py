"""
Flow automation layer — production automation model on top of DevOS workflows.

Preserves:
  - Spatial Flow dimension / OrchestrationCanvas
  - brain.workflow + workflow_executor + workflow_store
  - UCIP / capability substrate / durable ExecutionJob snapshots

This module adds:
  - trigger descriptors (manual, webhook, schedule, event)
  - run modes (manual / test / production)
  - hybrid visual + script workflow helpers
  - enable/disable + versioning helpers
  - in-process durable run ledger for unit tests and local recovery
  - cancel / resume / idempotency coordination around run_from_snapshot

Durable multi-instance production runs continue to use ExecutionJob enqueue
(api/routes/workflow.py). This ledger does not replace the job queue; it
complements snapshot-based resumability for the automation API.
"""

from __future__ import annotations

import os
from pathlib import Path

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.workflow_executor import (
    ExecutionState,
    OrchestrationResult,
    run_from_snapshot,
    JOB_SUCCEEDED,
    JOB_FAILED,
    STEP_SUCCEEDED,
)
from governance.reliability import scrub_secrets

logger = logging.getLogger("devos.flow_automation")


class TriggerType(str, Enum):
    MANUAL = "manual"
    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    EVENT = "event"


class RunMode(str, Enum):
    MANUAL = "manual"
    TEST = "test"
    PRODUCTION = "production"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"


@dataclass
class TriggerSpec:
    type: TriggerType
    enabled: bool = True
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value if isinstance(self.type, TriggerType) else self.type,
            "enabled": self.enabled,
            "config": scrub_secrets(dict(self.config or {})),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TriggerSpec":
        return cls(
            type=TriggerType(str(data.get("type") or "manual")),
            enabled=bool(data.get("enabled", True)),
            config=dict(data.get("config") or {}),
        )


@dataclass
class AutomationRun:
    run_id: str
    workflow_id: str
    workflow_version: int
    owner_id: str
    tenant_id: Optional[str]
    mode: RunMode
    status: RunStatus
    trigger: TriggerSpec
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None
    execution_state: dict = field(default_factory=dict)
    result_summary: dict = field(default_factory=dict)
    error: Optional[str] = None
    cancel_requested: bool = False
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return scrub_secrets({
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
            "mode": self.mode.value if isinstance(self.mode, RunMode) else self.mode,
            "status": self.status.value if isinstance(self.status, RunStatus) else self.status,
            "trigger": self.trigger.to_dict() if isinstance(self.trigger, TriggerSpec) else self.trigger,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "execution_state": self.execution_state,
            "result_summary": self.result_summary,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })

    @classmethod
    def from_dict(cls, data: dict) -> "AutomationRun":
        mode = data.get("mode") or RunMode.MANUAL.value
        status = data.get("status") or RunStatus.QUEUED.value
        try:
            mode_e = RunMode(mode)
        except Exception:
            mode_e = RunMode.MANUAL
        try:
            status_e = RunStatus(status)
        except Exception:
            status_e = RunStatus.QUEUED
        trig = data.get("trigger") or {}
        if isinstance(trig, dict):
            trigger = TriggerSpec.from_dict(trig) if hasattr(TriggerSpec, "from_dict") else TriggerSpec(
                type=str(trig.get("type") or "manual"),
                config=dict(trig.get("config") or {}),
            )
        else:
            trigger = TriggerSpec(type="manual")
        return cls(
            run_id=str(data.get("run_id") or ""),
            workflow_id=str(data.get("workflow_id") or ""),
            workflow_version=int(data.get("workflow_version") or 0),
            owner_id=str(data.get("owner_id") or ""),
            tenant_id=data.get("tenant_id"),
            mode=mode_e,
            status=status_e,
            trigger=trigger,
            idempotency_key=data.get("idempotency_key"),
            correlation_id=data.get("correlation_id"),
            execution_state=dict(data.get("execution_state") or {}),
            result_summary=dict(data.get("result_summary") or {}),
            error=data.get("error"),
            cancel_requested=bool(data.get("cancel_requested")),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex


class AutomationRunStore:
    """Durable automation run ledger.

    Serializes to disk so process restart can resume non-terminal runs.
    Multi-node production still prefers ExecutionJob + workflow snapshot;
    this store is the single-node coordination ledger used by flow_automation.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._runs: dict[str, AutomationRun] = {}
        self._by_idem: dict[str, str] = {}
        self._path = path or os.environ.get(
            "DEVOS_AUTOMATION_RUN_STORE",
            str(Path("data") / "automation_runs.json"),
        )
        self._load()

    def _load(self) -> None:
        try:
            p = Path(self._path)
            if not p.is_file():
                return
            import json
            raw = json.loads(p.read_text(encoding="utf-8"))
            for item in raw.get("runs") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    run = AutomationRun.from_dict(item) if hasattr(AutomationRun, "from_dict") else None
                    if run is None:
                        # best-effort field reconstruct
                        run = AutomationRun(**{
                            k: item[k] for k in item
                            if k in getattr(AutomationRun, "__dataclass_fields__", {})
                        })
                    self._runs[run.run_id] = run
                    if run.idempotency_key:
                        self._by_idem[f"{run.owner_id}:{run.idempotency_key}"] = run.run_id
                except Exception:
                    continue
        except Exception:
            pass

    def _persist(self) -> None:
        try:
            import json
            p = Path(self._path)
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "runs": [
                    (r.to_dict() if hasattr(r, "to_dict") else r.__dict__)
                    for r in self._runs.values()
                ]
            }
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass

    def put(self, run: AutomationRun) -> AutomationRun:
        run.updated_at = _now()
        self._runs[run.run_id] = run
        if run.idempotency_key:
            self._by_idem[f"{run.owner_id}:{run.idempotency_key}"] = run.run_id
        self._persist()
        return run

    def get(self, run_id: str) -> Optional[AutomationRun]:
        return self._runs.get(run_id)

    def get_by_idempotency(self, owner_id: str, key: str) -> Optional[AutomationRun]:
        rid = self._by_idem.get(f"{owner_id}:{key}")
        return self._runs.get(rid) if rid else None

    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[AutomationRun]:
        rows = [r for r in self._runs.values() if r.owner_id == owner_id]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]


_STORE = AutomationRunStore()


def get_run_store() -> AutomationRunStore:
    return _STORE


def reset_run_store_for_tests() -> AutomationRunStore:
    global _STORE
    import tempfile
    _STORE = AutomationRunStore(path=tempfile.mktemp(suffix="_automation_runs.json"))
    return _STORE


def build_snapshot_from_workflow(
    wf: Workflow,
    *,
    correlation_id: Optional[str] = None,
    enabled_check: bool = True,
) -> dict:
    """Build an immutable execution snapshot (mirrors store semantics for tests)."""
    if enabled_check and getattr(wf, "enabled", True) is False:
        raise ValueError("workflow is disabled")
    version = int(getattr(wf, "revision", None) or 0)
    try:
        version = int(str(wf.version).split(".")[0]) if not version else version
    except Exception:
        version = version or 1
    definition = {
        "workflow_id": wf.workflow_id,
        "name": wf.name,
        "description": wf.description,
        "version": wf.version,
        "start_step": wf.start_step,
        "triggers": list(wf.triggers or ["manual"]),
        "schedule": wf.schedule,
        "tags": list(wf.tags or []),
        "metadata": dict(wf.metadata or {}),
        "status": wf.status,
        "owner_id": wf.owner_id,
        "steps": [s.to_dict() for s in wf.steps],
    }
    from datetime import datetime, timezone
    return scrub_secrets({
        "schema_version": 1,
        "workflow_id": wf.workflow_id,
        "workflow_version": version or 1,
        "owner_id": wf.owner_id or "unknown",
        "tenant_id": getattr(wf, "tenant_id", None),
        "correlation_id": correlation_id or _id(),
        "definition": definition,
        "name": wf.name,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "status": wf.status,
        "enabled": bool(getattr(wf, "enabled", True)),
    })


def parse_triggers(wf: Workflow) -> list[TriggerSpec]:
    out: list[TriggerSpec] = []
    for t in wf.triggers or ["manual"]:
        if isinstance(t, dict):
            out.append(TriggerSpec.from_dict(t))
        else:
            try:
                out.append(TriggerSpec(type=TriggerType(str(t))))
            except Exception:
                out.append(TriggerSpec(type=TriggerType.MANUAL, config={"raw": t}))
    if wf.schedule:
        out.append(TriggerSpec(type=TriggerType.SCHEDULE, config={"cron": wf.schedule}))
    return out


def hybrid_script_step(
    step_id: str,
    *,
    code: str,
    language: str = "python",
    next_step: Optional[str] = None,
    timeout_s: int = 60,
    capability: Optional[str] = None,
) -> WorkflowStep:
    """Construct a first-class SCRIPT step for hybrid visual/script workflows."""
    return WorkflowStep(
        id=step_id,
        type=StepType.SCRIPT,
        name=f"script:{language}",
        capability=capability,
        inputs={"code": code, "language": language},
        next_step=next_step,
        timeout_s=timeout_s,
    )


def hybrid_http_step(
    step_id: str,
    *,
    url: str,
    method: str = "GET",
    next_step: Optional[str] = None,
    capability: str = "ucip:api.call",
) -> WorkflowStep:
    return WorkflowStep(
        id=step_id,
        type=StepType.HTTP,
        name="http",
        capability=capability,
        inputs={"url": url, "method": method},
        next_step=next_step,
        timeout_s=30,
    )


async def execute_automation(
    wf: Workflow,
    *,
    owner_id: str,
    tenant_id: Optional[str] = None,
    mode: RunMode = RunMode.MANUAL,
    trigger: Optional[TriggerSpec] = None,
    idempotency_key: Optional[str] = None,
    extra_context: Optional[dict] = None,
    execution_state: Optional[dict] = None,
    store: Optional[AutomationRunStore] = None,
) -> AutomationRun:
    """
    Execute a workflow snapshot through the existing orchestration runtime.

    Idempotency: same owner + key returns prior terminal run without re-executing.
    Resume: pass execution_state from a prior run.
    Cancel: if run.cancel_requested before start, mark cancelled.
    """
    store = store or get_run_store()
    if idempotency_key:
        prior = store.get_by_idempotency(owner_id, idempotency_key)
        if prior and prior.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            return prior

    trigger = trigger or TriggerSpec(type=TriggerType.MANUAL)
    correlation_id = _id()
    snap = build_snapshot_from_workflow(wf, correlation_id=correlation_id)
    run = AutomationRun(
        run_id=_id(),
        workflow_id=str(snap["workflow_id"]),
        workflow_version=int(snap["workflow_version"]),
        owner_id=owner_id,
        tenant_id=tenant_id,
        mode=mode,
        status=RunStatus.RUNNING,
        trigger=trigger,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        execution_state=dict(execution_state or {}),
        created_at=_now(),
        updated_at=_now(),
    )
    store.put(run)

    if run.cancel_requested:
        run.status = RunStatus.CANCELLED
        run.error = "cancelled before execution"
        store.put(run)
        return run

    ctx = {
        "owner_id": owner_id,
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "run_mode": mode.value if isinstance(mode, RunMode) else mode,
        **(extra_context or {}),
    }
    # Test mode: prefer dry isolation failures visible without production side effects
    if mode == RunMode.TEST:
        ctx["flow_test_mode"] = True

    result: OrchestrationResult = await run_from_snapshot(
        snap,
        execution_state=execution_state,
        job_id=None,
        extra_context=ctx,
    )
    run.execution_state = result.execution_state or {}
    run.result_summary = scrub_secrets({
        "status": result.status,
        "steps": [s if isinstance(s, dict) else getattr(s, "to_dict", lambda: s)() for s in (result.steps or [])][:50],
        "error_code": result.error_code,
    })
    run.error = result.error
    if result.status == JOB_SUCCEEDED:
        run.status = RunStatus.SUCCEEDED
    else:
        run.status = RunStatus.FAILED
    store.put(run)
    return run


async def resume_automation(
    run_id: str,
    wf: Workflow,
    *,
    store: Optional[AutomationRunStore] = None,
) -> AutomationRun:
    store = store or get_run_store()
    run = store.get(run_id)
    if not run:
        raise KeyError(f"run not found: {run_id}")
    if run.status == RunStatus.SUCCEEDED:
        return run  # idempotent
    if run.cancel_requested:
        run.status = RunStatus.CANCELLED
        store.put(run)
        return run
    return await execute_automation(
        wf,
        owner_id=run.owner_id,
        tenant_id=run.tenant_id,
        mode=run.mode,
        trigger=run.trigger,
        idempotency_key=None,  # resume is explicit
        execution_state=run.execution_state,
        store=store,
    )


def request_cancel(run_id: str, *, store: Optional[AutomationRunStore] = None) -> Optional[AutomationRun]:
    store = store or get_run_store()
    run = store.get(run_id)
    if not run:
        return None
    run.cancel_requested = True
    if run.status == RunStatus.RUNNING:
        # Soft cancel flag; executor checks between steps via state when wired
        run.status = RunStatus.CANCELLED
        run.error = run.error or "cancel requested"
    store.put(run)
    return run


def set_workflow_enabled(wf: Workflow, enabled: bool) -> Workflow:
    setattr(wf, "enabled", bool(enabled))
    return wf


def bump_workflow_version(wf: Workflow) -> Workflow:
    try:
        major = int(str(wf.version).split(".")[0])
        wf.version = f"{major + 1}.0.0"
    except Exception:
        wf.version = "2.0.0"
    rev = int(getattr(wf, "revision", 0) or 0) + 1
    setattr(wf, "revision", rev)
    return wf


# ── Durable run persistence (Postgres/SQLite via ORM) ─────────────────────────


def trigger_idempotency_key(
    *,
    workflow_id: str,
    workflow_version: int,
    trigger: TriggerSpec,
    delivery_id: Optional[str] = None,
    schedule_occurrence: Optional[str] = None,
) -> Optional[str]:
    """Build logical operation identity for a trigger delivery.

    - manual without delivery_id → None (new operation each time unless caller supplies key)
    - webhook/event → requires delivery_id (provider event/delivery id)
    - schedule → requires schedule_occurrence (e.g. 2026-09-18T12:00:00Z slot)
    """
    t = trigger.type if isinstance(trigger.type, TriggerType) else TriggerType(str(trigger.type))
    if t == TriggerType.MANUAL and not delivery_id:
        return None
    parts = {
        "workflow_id": workflow_id,
        "version": int(workflow_version),
        "trigger": t.value,
        "delivery_id": delivery_id or "",
        "schedule_occurrence": schedule_occurrence or "",
    }
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def publish_workflow_version(wf: Workflow) -> Workflow:
    """Mark workflow as published (executable). Definition is snapshotted at trigger time.

    Editing after publish should bump revision via bump_workflow_version / store update;
    runs always carry definition_snapshot from the version used at trigger.
    """
    setattr(wf, "status", "published")
    setattr(wf, "enabled", bool(getattr(wf, "enabled", True)))
    return wf


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def persist_run_durable(run: AutomationRun) -> bool:
    """Upsert automation run into automation_run_records. Best-effort durable."""
    try:
        from core.database import AsyncSessionLocal, AutomationRunRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AutomationRunRecord, run.run_id)
            payload = {
                "workflow_id": run.workflow_id,
                "workflow_version": int(run.workflow_version or 1),
                "owner_id": run.owner_id,
                "tenant_id": run.tenant_id,
                "status": run.status.value if isinstance(run.status, RunStatus) else str(run.status),
                "mode": run.mode.value if isinstance(run.mode, RunMode) else str(run.mode),
                "trigger_type": (
                    run.trigger.type.value
                    if run.trigger and isinstance(run.trigger.type, TriggerType)
                    else (run.trigger.type if run.trigger else "manual")
                ),
                "trigger_identity": (run.trigger.config or {}).get("delivery_id")
                or (run.trigger.config or {}).get("schedule_occurrence")
                if run.trigger
                else None,
                "idempotency_key": run.idempotency_key,
                "operation_id": getattr(run, "operation_id", None),
                "job_id": getattr(run, "execution_job_id", None) or (run.execution_state or {}).get("job_id"),
                "correlation_id": run.correlation_id,
                "definition_snapshot": scrub_secrets(dict(run.execution_state or {})),
                "result_summary": scrub_secrets(dict(run.result_summary or {})) if run.result_summary else None,
                "error": (run.error or "")[:4000] or None,
                "cancel_requested": bool(run.cancel_requested),
                "updated_at": _utcnow_naive(),
            }
            # Prefer explicit attributes when set by trigger path
            if getattr(run, "operation_id", None):
                payload["operation_id"] = run.operation_id
            if getattr(run, "execution_job_id", None):
                payload["job_id"] = run.execution_job_id
            if getattr(run, "definition_snapshot", None):
                payload["definition_snapshot"] = scrub_secrets(dict(run.definition_snapshot))
            if row is None:
                row = AutomationRunRecord(id=run.run_id, created_at=_utcnow_naive(), **{
                    k: v for k, v in payload.items() if k != "updated_at"
                })
                row.updated_at = payload["updated_at"]
                db.add(row)
            else:
                for k, v in payload.items():
                    setattr(row, k, v)
            await db.commit()
            return True
    except Exception:
        logger.warning("persist_run_durable failed", exc_info=True)
        return False


async def load_run_durable(run_id: str) -> Optional[AutomationRun]:
    try:
        from core.database import AsyncSessionLocal, AutomationRunRecord
        async with AsyncSessionLocal() as db:
            row = await db.get(AutomationRunRecord, run_id)
            if not row:
                return None
            return _row_to_run(row)
    except Exception:
        logger.debug("load_run_durable failed", exc_info=True)
        return None


def _row_to_run(row) -> AutomationRun:
    trigger = TriggerSpec(
        type=TriggerType(row.trigger_type or "manual"),
        enabled=True,
        config={
            k: v
            for k, v in {
                "delivery_id": row.trigger_identity if row.trigger_type in ("webhook", "event") else None,
                "schedule_occurrence": row.trigger_identity if row.trigger_type == "schedule" else None,
            }.items()
            if v
        },
    )
    run = AutomationRun(
        run_id=row.id,
        workflow_id=row.workflow_id,
        workflow_version=int(row.workflow_version or 1),
        owner_id=row.owner_id,
        tenant_id=row.tenant_id,
        mode=RunMode(row.mode) if row.mode in {m.value for m in RunMode} else RunMode.MANUAL,
        status=RunStatus(row.status) if row.status in {s.value for s in RunStatus} else RunStatus.QUEUED,
        trigger=trigger,
        idempotency_key=row.idempotency_key,
        correlation_id=row.correlation_id or "",
        execution_state=dict(row.definition_snapshot or {}),
        result_summary=dict(row.result_summary or {}) if row.result_summary else {},
        error=row.error,
        cancel_requested=bool(row.cancel_requested),
        created_at=row.created_at or _utcnow_naive(),
        updated_at=row.updated_at or _utcnow_naive(),
    )
    setattr(run, "operation_id", row.operation_id)
    setattr(run, "execution_job_id", row.job_id)
    setattr(run, "definition_snapshot", dict(row.definition_snapshot or {}))
    return run


async def find_run_by_idempotency_durable(
    owner_id: str, idempotency_key: str
) -> Optional[AutomationRun]:
    if not idempotency_key:
        return None
    try:
        from core.database import AsyncSessionLocal, AutomationRunRecord
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(AutomationRunRecord)
                .where(
                    AutomationRunRecord.owner_id == owner_id,
                    AutomationRunRecord.idempotency_key == idempotency_key,
                )
                .order_by(AutomationRunRecord.created_at.desc())
                .limit(1)
            )
            row = r.scalar_one_or_none()
            return _row_to_run(row) if row else None
    except Exception:
        logger.debug("find_run_by_idempotency_durable failed", exc_info=True)
        return None


async def trigger_automation_run(
    wf: Workflow,
    *,
    owner_id: str,
    tenant_id: Optional[str] = None,
    mode: RunMode = RunMode.PRODUCTION,
    trigger: Optional[TriggerSpec] = None,
    delivery_id: Optional[str] = None,
    schedule_occurrence: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    store: Optional[AutomationRunStore] = None,
) -> AutomationRun:
    """Resolve trigger → durable run + ExecutionJob/Operation (authoritative spine).

    Disabled automations reject new triggers. Already-running executions are not
    cancelled by disable. Idempotent triggers converge on existing run/op.
    """
    store = store or get_run_store()
    if getattr(wf, "enabled", True) is False:
        raise PermissionError("automation_disabled")

    status = (getattr(wf, "status", None) or "draft").lower()
    # Allow draft in tests; production triggers should prefer published
    version = int(getattr(wf, "revision", None) or 0) or 1
    try:
        if not getattr(wf, "revision", None) and getattr(wf, "version", None):
            version = int(str(wf.version).split(".")[0]) or version
    except Exception:
        pass

    trigger = trigger or TriggerSpec(type=TriggerType.MANUAL)
    if delivery_id and trigger.config is not None:
        trigger.config = dict(trigger.config or {})
        trigger.config["delivery_id"] = delivery_id
    if schedule_occurrence and trigger.config is not None:
        trigger.config = dict(trigger.config or {})
        trigger.config["schedule_occurrence"] = schedule_occurrence

    ikey = idempotency_key or trigger_idempotency_key(
        workflow_id=str(wf.workflow_id),
        workflow_version=version,
        trigger=trigger,
        delivery_id=delivery_id,
        schedule_occurrence=schedule_occurrence,
    )

    if ikey:
        prior = await find_run_by_idempotency_durable(owner_id, ikey)
        if prior is None:
            prior = store.get_by_idempotency(owner_id, ikey)
        if prior and prior.status in (
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
        ):
            return prior

    correlation_id = _id()
    snap = build_snapshot_from_workflow(wf, correlation_id=correlation_id, enabled_check=True)
    definition_snapshot = scrub_secrets(dict(snap))

    run = AutomationRun(
        run_id=_id(),
        workflow_id=str(snap["workflow_id"]),
        workflow_version=int(snap["workflow_version"]),
        owner_id=owner_id,
        tenant_id=tenant_id,
        mode=mode,
        status=RunStatus.QUEUED,
        trigger=trigger,
        idempotency_key=ikey,
        correlation_id=correlation_id,
        execution_state={},
        created_at=_now(),
        updated_at=_now(),
    )
    setattr(run, "definition_snapshot", definition_snapshot)

    # Bind consequential execution via existing job queue (op reserved atomically)
    try:
        from workers.job_queue import enqueue
        job = await enqueue(
            owner_id=owner_id,
            tenant_id=tenant_id or owner_id,
            job_type="workflow",
            payload={
                "workflow_id": run.workflow_id,
                "workflow_version": run.workflow_version,
                "run_id": run.run_id,
                "snapshot": definition_snapshot,
                "trigger": trigger.to_dict(),
            },
            actor_id=owner_id,
            idempotency_key=ikey,
            correlation={"correlation_id": correlation_id, "run_id": run.run_id},
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
        )
        setattr(run, "execution_job_id", job.id)
        setattr(run, "operation_id", job.operation_id)
        run.status = RunStatus.QUEUED
        if job.operation_id:
            run.execution_state = {
                "job_id": job.id,
                "operation_id": job.operation_id,
            }
    except Exception as e:
        run.status = RunStatus.FAILED
        run.error = f"enqueue_failed:{type(e).__name__}:{str(e)[:200]}"
        store.put(run)
        await persist_run_durable(run)
        raise

    store.put(run)
    await persist_run_durable(run)
    return run
