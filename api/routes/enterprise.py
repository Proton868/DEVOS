"""
API — Enterprise routes (Agency OS Master Plan §7).

Exposes RBAC, audit logging, billing, and marketplace capabilities as REST
endpoints. All enterprise routes require at minimum TENANT_USER tier.
"""
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from api.routes.auth import get_current_user
from core.database import get_db

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])


# ── RBAC ──────────────────────────────────────────────────────────────────────

@router.get("/rbac/check")
async def rbac_check(
    request: Request,
    capability: str = Query(..., description="UCIP capability slug"),
    tenant_id: str = Query("default"),
    db=Depends(get_db),
):
    """Check if the current user can perform a capability."""
    await get_current_user(request, db)
    from governance.identity_context import IdentityContext, TenantTier
    from governance.rbac import evaluate_rbac

    user = await get_current_user(request, db)
    # Read the user's actual trust tier from the database, defaulting to TENANT_USER
    actual_tier = TenantTier.TENANT_USER
    try:
        if hasattr(user, "trust_tier") and user.trust_tier:
            actual_tier = TenantTier(user.trust_tier)
    except (ValueError, AttributeError):
        pass

    identity = IdentityContext(
        actor_id=user.username if hasattr(user, "username") else "user",
        tenant_id=tenant_id,
        trust_tier=actual_tier,
    )
    result = evaluate_rbac(identity, capability)
    return result.__dict__


@router.get("/rbac/tiers")
async def rbac_tiers(request: Request, db=Depends(get_db)):
    """List all tiers and their capabilities."""
    await get_current_user(request, db)
    from governance.identity_context import TenantTier
    from governance.rbac import RBACEngine

    return {
        "tiers": [
            {
                "tier": t.value,
                "rank": RBACEngine._tier_rank(t),
                "capabilities": RBACEngine.list_capabilities_for_tier(t),
            }
            for t in TenantTier
        ]
    }


# ── Audit ─────────────────────────────────────────────────────────────────────

@router.get("/audit")
async def audit_query(
    request: Request,
    actor_id: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    """Query audit log entries."""
    await get_current_user(request, db)
    from governance.audit import get_audit_logger, AuditEventType

    et = None
    if event_type:
        try:
            et = AuditEventType(event_type)
        except ValueError:
            return {"error": f"Invalid event_type: {event_type}"}

    audit = get_audit_logger()
    entries = audit.query(
        actor_id=actor_id,
        tenant_id=tenant_id,
        event_type=et,
        limit=limit,
        offset=offset,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/audit/stats")
async def audit_stats(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    db=Depends(get_db),
):
    """Get audit statistics."""
    await get_current_user(request, db)
    from governance.audit import get_audit_logger
    return get_audit_logger().stats(tenant_id=tenant_id)


# ── Billing ───────────────────────────────────────────────────────────────────

@router.get("/billing/usage")
async def billing_usage(
    request: Request,
    tenant_id: str = Query("default"),
    db=Depends(get_db),
):
    """Get current billing usage for a tenant."""
    await get_current_user(request, db)
    from governance.billing import get_billing
    usage = get_billing().get_usage(tenant_id)
    return {"usage": usage.to_dict()}


@router.get("/billing/events")
async def billing_events(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
):
    """List recent billing events."""
    await get_current_user(request, db)
    from governance.billing import get_billing
    return {"events": get_billing().list_events(tenant_id=tenant_id, limit=limit)}


# ── Marketplace ───────────────────────────────────────────────────────────────

@router.get("/marketplace")
async def marketplace_list(
    request: Request,
    category: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("downloads"),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
):
    """List marketplace capabilities."""
    await get_current_user(request, db)
    from governance.marketplace import get_marketplace
    entries = get_marketplace().list(
        category=category,
        tag=tag,
        search=search,
        sort_by=sort_by,
        limit=limit,
    )
    return {
        "entries": [e.to_dict() for e in entries],
        "count": len(entries),
    }


@router.get("/marketplace/categories")
async def marketplace_categories(request: Request, db=Depends(get_db)):
    """List all marketplace categories."""
    await get_current_user(request, db)
    from governance.marketplace import get_marketplace
    return {"categories": get_marketplace().categories()}


@router.get("/marketplace/{slug}")
async def marketplace_get(slug: str, request: Request, db=Depends(get_db)):
    """Get a marketplace entry by slug."""
    await get_current_user(request, db)
    from governance.marketplace import get_marketplace
    entry = get_marketplace().get(slug)
    if not entry:
        raise HTTPException(404, f"Marketplace entry not found: {slug}")
    get_marketplace().record_download(slug)
    return {"entry": entry.to_dict()}