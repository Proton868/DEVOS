"""Tenant / Membership helpers + RLS-style filters."""
from __future__ import annotations
import re
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import Tenant, Membership, User, gen_id

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
