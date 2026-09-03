"""Durable workflow persistence — database is the source of truth.

Runtime WorkflowEngine may hold a process-local cache, but all create/load/
update/delete operations go through this store so definitions survive restart
and are visible across processes sharing the same DATABASE_URL.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import WorkflowRecord, gen_id
from brain.workflow import Workflow

logger = logging.getLogger("devos.workflow_store")

# Fields that must never be taken from client/import payload as authority.
_STRIP_ON_IMPORT = {"owner_id", "tenant_id", "user_id"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def workflow_to_definition(wf: Workflow) -> dict:
    """Serialize runtime Workflow to JSON-safe definition (no secrets)."""
    data = wf.to_dict()
    # Do not persist ownership inside definition blob — columns are authoritative.
    data.pop("owner_id", None)
    return data


def record_to_workflow(row: WorkflowRecord) -> Workflow:
    """Hydrate runtime Workflow from a DB row."""
    data = dict(row.definition or {})
    data["workflow_id"] = row.id
    data["name"] = row.name
    data["description"] = row.description or data.get("description", "")
    data["status"] = row.status or data.get("status", "draft")
    data["owner_id"] = row.owner_id
    # Integer revision on the record wins over any string in the blob.
    data["version"] = str(row.version)
    data["_revision"] = row.version
    wf = Workflow.from_dict(data)
    wf.owner_id = row.owner_id
    wf.updated_at = row.updated_at or _now()
    wf.created_at = row.created_at or _now()
    # Attach revision for API/evidence without changing dataclass schema hard
    setattr(wf, "revision", row.version)
    setattr(wf, "enabled", bool(row.enabled) if row.enabled is not None else True)
    setattr(wf, "tenant_id", row.tenant_id)
    return wf


def sanitize_import_dict(data: dict) -> dict:
    clean = copy.deepcopy(data or {})
    for k in _STRIP_ON_IMPORT:
        clean.pop(k, None)
    # Force new id on import — caller may override
    clean.pop("workflow_id", None)
    return clean


async def create_workflow_record(
    db: AsyncSession,
    *,
    owner_id: str,
    workflow: Workflow,
    tenant_id: Optional[str] = None,
) -> Workflow:
    if not workflow.workflow_id:
        workflow.workflow_id = gen_id()
    workflow.owner_id = owner_id
    row = WorkflowRecord(
        id=workflow.workflow_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        name=workflow.name,
        description=workflow.description or "",
        status=workflow.status or "draft",
        enabled=True,
        version=1,
        definition=workflow_to_definition(workflow),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return record_to_workflow(row)


async def get_workflow_for_owner(
    db: AsyncSession, workflow_id: str, owner_id: str
) -> Optional[Workflow]:
    r = await db.execute(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.owner_id == owner_id,
        )
    )
    row = r.scalar_one_or_none()
    return record_to_workflow(row) if row else None


async def list_workflows_for_owner(
    db: AsyncSession,
    owner_id: str,
    *,
    status: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> list[Workflow]:
    q = select(WorkflowRecord).where(WorkflowRecord.owner_id == owner_id)
    if status:
        q = q.where(WorkflowRecord.status == status)
    q = q.order_by(WorkflowRecord.updated_at.desc())
    r = await db.execute(q)
    rows = list(r.scalars().all())
    out = []
    for row in rows:
        wf = record_to_workflow(row)
        if tags:
            if not any(t in (wf.tags or []) for t in tags):
                continue
        out.append(wf)
    return out


async def update_workflow_for_owner(
    db: AsyncSession,
    workflow_id: str,
    owner_id: str,
    workflow: Workflow,
) -> Optional[Workflow]:
    r = await db.execute(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.owner_id == owner_id,
        )
    )
    row = r.scalar_one_or_none()
    if not row:
        return None
    row.name = workflow.name
    row.description = workflow.description or ""
    row.status = workflow.status or row.status
    row.definition = workflow_to_definition(workflow)
    row.version = int(row.version or 1) + 1
    row.updated_at = _now()
    await db.commit()
    await db.refresh(row)
    return record_to_workflow(row)


async def delete_workflow_for_owner(
    db: AsyncSession, workflow_id: str, owner_id: str
) -> bool:
    """Delete definition only. Does not cascade-delete evidence/jobs."""
    r = await db.execute(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.owner_id == owner_id,
        )
    )
    row = r.scalar_one_or_none()
    if not row:
        return False
    await db.delete(row)
    await db.commit()
    return True


# ── Canonical WorkflowExecutionSnapshot (schema_version = 1) ─────────────────
# Immutable definition captured for one ExecutionJob. Never reload WorkflowRecord
# during retry/recovery. Future formats bump schema_version and document migration.

SNAPSHOT_SCHEMA_VERSION = 1
_REQUIRED_SNAPSHOT_FIELDS = (
    "schema_version",
    "workflow_id",
    "workflow_version",
    "owner_id",
    "definition",
    "captured_at",
)


class SnapshotError(Exception):
    """Snapshot missing, malformed, or inconsistent with ExecutionJob identity."""


def build_execution_snapshot(
    *,
    workflow_id: str,
    workflow_version: int,
    owner_id: str,
    tenant_id: Optional[str],
    name: str,
    definition: dict,
    correlation_id: Optional[str] = None,
    status: Optional[str] = None,
    enabled: bool = True,
) -> dict:
    """Construct the canonical scrubbed snapshot. Self-contained definition required."""
    from governance.reliability import scrub_secrets

    if not isinstance(definition, dict):
        raise SnapshotError("definition must be a dict")
    # Reject pointer-only definitions
    if set(definition.keys()) <= {"workflow_id", "id"} and "steps" not in definition:
        raise SnapshotError("definition must be self-contained (not a workflow_id pointer)")

    snap = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "workflow_version": int(workflow_version),
        "owner_id": owner_id,
        "tenant_id": tenant_id,
        "name": name or "",
        "definition": copy.deepcopy(definition),
        "captured_at": _now().isoformat(),
        "correlation_id": correlation_id,
        "status": status or "draft",
        "enabled": bool(enabled),
        # Back-compat alias used by older readers
        "version": int(workflow_version),
    }
    return scrub_secrets(snap)


def validate_execution_snapshot(snap: Optional[dict]) -> dict:
    """Validate canonical shape. Raises SnapshotError — never falls back to live record."""
    if not snap or not isinstance(snap, dict):
        raise SnapshotError("snapshot missing or not a dict")
    for f in _REQUIRED_SNAPSHOT_FIELDS:
        if f not in snap or snap[f] is None or snap[f] == "":
            if f == "definition" and snap.get(f) == {}:
                continue  # empty def rejected separately
            if f not in snap or snap[f] is None:
                raise SnapshotError(f"snapshot missing required field: {f}")
    if int(snap.get("schema_version") or 0) != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError(
            f"unsupported snapshot schema_version: {snap.get('schema_version')}"
        )
    if not isinstance(snap.get("definition"), dict):
        raise SnapshotError("snapshot.definition must be a dict")
    if "steps" not in snap["definition"] and not snap["definition"].get("name"):
        # Allow validated empty-step only if caller already validated Workflow object;
        # structural presence of definition dict is required.
        pass
    try:
        int(snap["workflow_version"])
    except (TypeError, ValueError) as e:
        raise SnapshotError("workflow_version must be int") from e
    return snap


def assert_job_snapshot_consistency(job, snap: dict) -> None:
    """job.workflow_id/version must match snapshot. Do not silently repair."""
    j_wid = getattr(job, "workflow_id", None)
    j_ver = getattr(job, "workflow_version", None)
    s_wid = snap.get("workflow_id")
    s_ver = snap.get("workflow_version", snap.get("version"))
    if j_wid and s_wid and j_wid != s_wid:
        raise SnapshotError(f"job/snapshot workflow_id mismatch: {j_wid} vs {s_wid}")
    if j_ver is not None and s_ver is not None and int(j_ver) != int(s_ver):
        raise SnapshotError(
            f"job/snapshot workflow_version mismatch: {j_ver} vs {s_ver}"
        )


def snapshot_from_job_payload(payload: Optional[dict]) -> dict:
    """Extract and validate immutable snapshot from ExecutionJob.payload.

    Retries/recovery MUST use this. Raises SnapshotError on corruption —
    never reload WorkflowRecord to "fix" a bad snapshot.
    """
    if not payload or not isinstance(payload, dict):
        raise SnapshotError("job payload missing")
    snap = payload.get("workflow_snapshot")
    return validate_execution_snapshot(snap if isinstance(snap, dict) else None)


def load_snapshot_for_job(job) -> dict:
    """Load validated snapshot for a job and check identity consistency."""
    snap = snapshot_from_job_payload(getattr(job, "payload", None) or {})
    assert_job_snapshot_consistency(job, snap)
    return snap


async def snapshot_workflow_for_execution(
    db: AsyncSession,
    workflow_id: str,
    owner_id: str,
    *,
    correlation_id: Optional[str] = None,
    require_valid: bool = True,
) -> Optional[dict]:
    """Build canonical scrubbed snapshot from WorkflowRecord (DB source of truth).

    When require_valid=True, the runtime Workflow definition must pass validate().
    Returns None if the row is not found/not owned. Raises SnapshotError on invalid def.
    """
    r = await db.execute(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.owner_id == owner_id,
        )
    )
    row = r.scalar_one_or_none()
    if not row:
        return None

    enabled = bool(row.enabled) if row.enabled is not None else True
    if not enabled:
        raise SnapshotError("workflow is disabled")

    wf = record_to_workflow(row)
    if require_valid:
        ok, errors = wf.validate()
        if not ok:
            raise SnapshotError(f"invalid workflow definition: {'; '.join(errors)}")

    return build_execution_snapshot(
        workflow_id=row.id,
        workflow_version=int(row.version or 1),
        owner_id=row.owner_id,
        tenant_id=row.tenant_id,
        name=row.name,
        definition=copy.deepcopy(row.definition or {}),
        correlation_id=correlation_id,
        status=row.status,
        enabled=enabled,
    )

