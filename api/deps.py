"""Shared FastAPI dependencies — auth + tenant scope."""
from __future__ import annotations
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db, User
from api.routes.auth import get_current_user
from governance.request_identity import get_tenant_context, TenantContext
from governance.ucip import TrustLevel

async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    return await get_current_user(request, db)

async def tenant_ctx(request: Request, db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user)) -> TenantContext:
    return await get_tenant_context(request, db, user, trust=TrustLevel.OPERATOR)
