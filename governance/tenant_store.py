"""Tenant helpers + scoped queries for local SQLAlchemy (parity with RLS)."""
from __future__ import annotations
import re
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import (
    Tenant, Membership, User, WorkflowRecord, EvidenceRecord, gen_id,
)

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:120] or "tenant"

async def ensure_personal_tenant(db: AsyncSession, user: User) -> Tenant:
    if getattr(user, "default_tenant_id", None):
        r = await db.execute(select(Tenant).where(Tenant.id == user.default_tenant_id))
        t = r.scalar_one_or_none()
        if t: return t
    slug = slugify(f"user-{user.username}")
    r = await db.execute(select(Tenant).where(Tenant.slug == slug))
    t = r.scalar_one_or_none()
    if not t:
        t = Tenant(id=gen_id(), name=f"{user.username}'s workspace", slug=slug, tier="tenant_user")
        db.add(t); await db.flush()
    mr = await db.execute(select(Membership).where(Membership.tenant_id==t.id, Membership.user_id==user.id))
    if not mr.scalar_one_or_none():
        db.add(Membership(id=gen_id(), tenant_id=t.id, user_id=user.id, role="admin"))
    user.default_tenant_id = t.id
    await db.commit(); await db.refresh(t)
    return t

async def user_tenant_ids(db, user_id) -> set:
    r = await db.execute(select(Membership.tenant_id).where(Membership.user_id==user_id))
    return {row[0] for row in r.all()}

async def is_tenant_member(db, user_id, tenant_id) -> bool:
    r = await db.execute(select(Membership.id).where(Membership.user_id==user_id, Membership.tenant_id==tenant_id))
    return r.scalar_one_or_none() is not None

async def is_tenant_admin(db, user_id, tenant_id) -> bool:
    r = await db.execute(select(Membership.role).where(Membership.user_id==user_id, Membership.tenant_id==tenant_id))
    return r.scalar_one_or_none() in ("admin", "owner", "operator")

async def require_tenant_access(db, user_id, tenant_id, *, admin=False) -> bool:
    return await is_tenant_admin(db, user_id, tenant_id) if admin else await is_tenant_member(db, user_id, tenant_id)

async def list_workflows_for_user(db, user_id, tenant_id=None):
    if tenant_id:
        if not await is_tenant_member(db, user_id, tenant_id): return []
        r = await db.execute(select(WorkflowRecord).where(WorkflowRecord.tenant_id==tenant_id)); return list(r.scalars().all())
    tids = await user_tenant_ids(db, user_id)
    if not tids:
        r = await db.execute(select(WorkflowRecord).where(WorkflowRecord.owner_id==user_id)); return list(r.scalars().all())
    r = await db.execute(select(WorkflowRecord).where(WorkflowRecord.tenant_id.in_(tids))); return list(r.scalars().all())

async def list_evidence_for_user(db, user_id, tenant_id=None):
    if tenant_id:
        if not await is_tenant_member(db, user_id, tenant_id): return []
        r = await db.execute(select(EvidenceRecord).where(EvidenceRecord.tenant_id==tenant_id)); return list(r.scalars().all())
    tids = await user_tenant_ids(db, user_id)
    if not tids:
        r = await db.execute(select(EvidenceRecord).where(EvidenceRecord.owner_id==user_id)); return list(r.scalars().all())
    r = await db.execute(select(EvidenceRecord).where(EvidenceRecord.tenant_id.in_(tids))); return list(r.scalars().all())

async def list_jobs_for_user(db, user_id, tenant_id=None, limit=100):
    from core.database import ExecutionJob
    if tenant_id:
        if not await is_tenant_member(db, user_id, tenant_id): return []
        r = await db.execute(select(ExecutionJob).where(ExecutionJob.tenant_id==tenant_id).order_by(ExecutionJob.created_at.desc()).limit(limit))
        return list(r.scalars().all())
    r = await db.execute(select(ExecutionJob).where(ExecutionJob.owner_id==user_id).order_by(ExecutionJob.created_at.desc()).limit(limit))
    return list(r.scalars().all())
