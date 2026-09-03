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


async def snapshot_workflow_for_execution(
    db: AsyncSession, workflow_id: str, owner_id: str
) -> Optional[dict]:
    """Return an immutable snapshot {definition, version, workflow_id, owner_id}
    for execution/evidence. Running jobs must use this snapshot, not a live reload.
    Secrets are scrubbed via governance.reliability.scrub_secrets.
    """
    from governance.reliability import scrub_secrets

    r = await db.execute(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.owner_id == owner_id,
        )
    )
    row = r.scalar_one_or_none()
    if not row:
        return None
    return scrub_secrets({
        "workflow_id": row.id,
        "owner_id": row.owner_id,
        "tenant_id": row.tenant_id,
        "version": int(row.version or 1),
        "name": row.name,
        "definition": copy.deepcopy(row.definition or {}),
        "status": row.status,
        "enabled": bool(row.enabled) if row.enabled is not None else True,
    })


def snapshot_from_job_payload(payload: Optional[dict]) -> Optional[dict]:
    """Extract the immutable workflow snapshot embedded in an ExecutionJob payload.
    Retries/recovery MUST use this, never a live WorkflowRecord reload.
    """
    if not payload:
        return None
    snap = payload.get("workflow_snapshot")
    if not isinstance(snap, dict):
        return None
    return snap

