"""
API — Capabilities route (Agency OS Master Plan §1 Capability Registry).

Exposes the CapabilityRegistry as a REST API so Workers, the Cognitive System,
and the UI can discover what capabilities are available, what trust level
they require, and what their input/output schemas look like.
"""
from fastapi import APIRouter, Depends, Request, Query

from api.routes.auth import get_current_user
from core.database import get_db
from governance.capability_registry import (
    get_registry, CapabilityCategory, CapabilityRisk,
)

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("")
async def list_capabilities(
    request: Request,
    category: str = Query(None, description="Filter by category"),
    risk: str = Query(None, description="Filter by max risk level"),
    trust: str = Query(None, description="Filter by trust level"),
    db=Depends(get_db),
):
    """List all registered capabilities with optional filtering."""
    await get_current_user(request, db)
    registry = get_registry()

    caps = registry.list_all()
    if category:
        try:
            cat = CapabilityCategory(category)
            caps = [c for c in caps if c.category == cat]
        except ValueError:
            return {"error": f"Invalid category: {category}. Valid: {[c.value for c in CapabilityCategory]}"}

    if risk:
        try:
            risk_level = CapabilityRisk(risk)
            caps = [c for c in caps if c.risk.value <= risk_level.value]
        except ValueError:
            return {"error": f"Invalid risk: {risk}. Valid: {[r.value for r in CapabilityRisk]}"}

    if trust:
        caps = registry.list_by_trust(trust)

    return {
        "capabilities": [c.to_dict() for c in caps],
        "count": len(caps),
    }


@router.get("/categories")
async def list_categories(request: Request, db=Depends(get_db)):
    """List capability categories with counts."""
    await get_current_user(request, db)
    registry = get_registry()
    return {"categories": registry.categories()}


@router.get("/{slug}")
async def get_capability(slug: str, request: Request, db=Depends(get_db)):
    """Get a single capability by slug."""
    await get_current_user(request, db)
    registry = get_registry()
    cap = registry.get(slug)
    if not cap:
        return {"error": f"Capability not found: {slug}"}
    return {"capability": cap.to_dict()}