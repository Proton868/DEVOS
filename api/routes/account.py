"""Account plan/profile/onboarding — identity layer only. Not UCIP authority."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_user, get_db, ensure_personal_tenant
from core.database import User
from core.config import settings
from governance.identity_contract import reject_client_authority_fields

router = APIRouter(prefix="/api/account", tags=["account"])

from core.account_constants import PUBLIC_PLANS, ALL_PLANS, ROLES


def user_public(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "is_admin": bool(u.is_admin),
        "role": getattr(u, "role", None) or ("hegemon" if u.is_admin else "member"),
        "plan": getattr(u, "plan", None) or ("hegemon" if u.is_admin else "recruit"),
        "onboarding_status": getattr(u, "onboarding_status", None) or "NOT_STARTED",
        "display_name": getattr(u, "display_name", None) or u.username,
        "preferred_name": getattr(u, "preferred_name", None),
        "avatar_url": getattr(u, "avatar_url", None),
        "bio": getattr(u, "bio", None),
        "job_title": getattr(u, "job_title", None),
        "organization": getattr(u, "organization", None),
        "timezone": getattr(u, "timezone", None),
    }


@router.get("/me")
async def account_me(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    return user_public(user)


class PlanBody(BaseModel):
    plan: str = Field(..., min_length=3, max_length=32)


@router.post("/plan")
async def select_plan(body: PlanBody, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    plan = body.plan.lower().strip().replace(" ", "_").replace("-", "_")
    if plan in ("elder", "hegemon"):
        raise HTTPException(403, "Elder and Hegemon are not public plan selections")
    if plan not in PUBLIC_PLANS:
        raise HTTPException(400, f"Invalid plan. Choose one of: {', '.join(PUBLIC_PLANS)}")
    # Idempotent: already past plan selection keeps plan unless still onboarding
    status = getattr(user, "onboarding_status", None) or "NOT_STARTED"
    user.plan = plan
    if status in ("NOT_STARTED", "PLAN_SELECTED", None, ""):
        user.onboarding_status = "PROFILE_PENDING"
    await db.commit()
    await db.refresh(user)
    return user_public(user)


class ProfileBody(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)
    preferred_name: Optional[str] = Field(None, max_length=128)
    avatar_url: Optional[str] = Field(None, max_length=512)
    bio: Optional[str] = Field(None, max_length=2000)
    job_title: Optional[str] = Field(None, max_length=128)
    organization: Optional[str] = Field(None, max_length=128)
    timezone: Optional[str] = Field(None, max_length=64)


@router.patch("/profile")
async def update_profile(body: ProfileBody, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    # Hostile client may send role/plan/account_id — strip non-profile authority fields
    data = reject_client_authority_fields(body.model_dump(exclude_unset=True))
    allowed = {"display_name", "preferred_name", "avatar_url", "bio", "job_title", "organization", "timezone"}
    data = {k: v for k, v in data.items() if k in allowed}
    for k, v in data.items():
        setattr(user, k, v)
    status = getattr(user, "onboarding_status", None) or "NOT_STARTED"
    if status in ("PROFILE_PENDING", "PLAN_SELECTED"):
        user.onboarding_status = "TOUR_PENDING"
    await db.commit()
    await db.refresh(user)
    return user_public(user)


class OnboardingBody(BaseModel):
    status: str  # COMPLETED | SKIPPED | TOUR_PENDING | PROFILE_PENDING


@router.post("/onboarding")
async def set_onboarding(body: OnboardingBody, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    st = body.status.upper().strip()
    allowed = {"NOT_STARTED", "PLAN_SELECTED", "PROFILE_PENDING", "TOUR_PENDING", "COMPLETED", "SKIPPED"}
    if st not in allowed:
        raise HTTPException(400, "Invalid onboarding status")
    user.onboarding_status = st
    await db.commit()
    await db.refresh(user)
    return user_public(user)


@router.post("/bootstrap-owner")
async def bootstrap_owner(request: Request, db=Depends(get_db)):
    """Ensure configured owner/admin is Hegemon. Server-side only; not a frontend grant."""
    user = await get_current_user(request, db)
    owner_user = (getattr(settings, "ADMIN_USERNAME", None) or "admin").lower()
    if user.is_admin or (user.username or "").lower() == owner_user:
        user.role = "hegemon"
        user.plan = "hegemon"
        if (getattr(user, "onboarding_status", None) or "") in ("NOT_STARTED", ""):
            user.onboarding_status = "COMPLETED"
        await db.commit()
        await db.refresh(user)
    return user_public(user)
