"""
API — Evidence route (Agency OS Master Plan §1 EvidenceChain).

Ownership: authenticated user may only list/read chains whose identity_context
matches their user_id. Missing owner metadata is treated as inaccessible (404).
"""
from fastapi import APIRouter, Depends, Request, Query, HTTPException

from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant

from core.database import get_db
from governance.evidence import EvidenceChainManager

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


def _require_chain_owner(chain, user_id: str, world_id: str | None = None):
    if not chain or not EvidenceChainManager.owned_by(chain, user_id):
        raise HTTPException(404, "Evidence chain not found")
    # World binding when chain carries tenant/world metadata
    if world_id:
        meta = getattr(chain, "identity_context", None) or getattr(chain, "metadata", None) or {}
        if isinstance(meta, dict):
            cw = str(meta.get("world_id") or meta.get("tenant_id") or "").strip()
            if cw and cw != str(world_id).strip():
                raise HTTPException(404, "Evidence chain not found")
    return chain


@router.get("/chains")
async def list_chains(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
):
    """List recent evidence chains for the authenticated user only."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    chains = EvidenceChainManager.list_recent(limit, user_id=str(user.id))
    return {"chains": chains, "count": len(chains)}


@router.get("/chains/{chain_id}")
async def get_chain(chain_id: str, request: Request, db=Depends(get_db)):
    """Get a single evidence chain by ID (owner + world only)."""
    user = await get_current_user(request, db)
    from governance.request_identity import get_tenant_context
    from governance.ucip import TrustLevel
    tctx = await get_tenant_context(request, db, user, trust=TrustLevel.OPERATOR)
    chain = EvidenceChainManager.load(chain_id)
    _require_chain_owner(chain, str(user.id), world_id=tctx.world.world_id)
    return {"chain": chain.to_dict()}


@router.get("/chains/{chain_id}/replay")
async def replay_chain(chain_id: str, request: Request, db=Depends(get_db)):
    """Full replay of a chain — every node in topological order (owner only)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    chain = EvidenceChainManager.load(chain_id)
    _require_chain_owner(chain, str(user.id))
    result = EvidenceChainManager.replay(chain_id)
    return result


@router.get("/chains/{chain_id}/stats")
async def chain_stats(chain_id: str, request: Request, db=Depends(get_db)):
    """Get statistics for a chain (owner only)."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    chain = EvidenceChainManager.load(chain_id)
    _require_chain_owner(chain, str(user.id))
    return {"stats": chain.stats()}
