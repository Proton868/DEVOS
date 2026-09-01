"""
API — Evidence route (Agency OS Master Plan §1 EvidenceChain).

Exposes the EvidenceChain DAG for audit replay, analytics, and compliance.
Every execution run produces an EvidenceChain that can be walked and replayed.
"""
from fastapi import APIRouter, Depends, Request, Query

from api.routes.auth import get_current_user
from core.database import get_db
from governance.evidence import EvidenceChainManager

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


@router.get("/chains")
async def list_chains(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
):
    """List recent evidence chains."""
    await get_current_user(request, db)
    chains = EvidenceChainManager.list_recent(limit)
    return {"chains": chains, "count": len(chains)}


@router.get("/chains/{chain_id}")
async def get_chain(chain_id: str, request: Request, db=Depends(get_db)):
    """Get a single evidence chain by ID."""
    await get_current_user(request, db)
    chain = EvidenceChainManager.load(chain_id)
    if not chain:
        return {"error": f"Evidence chain not found: {chain_id}"}
    return {"chain": chain.to_dict()}


@router.get("/chains/{chain_id}/replay")
async def replay_chain(chain_id: str, request: Request, db=Depends(get_db)):
    """Full replay of a chain — every node in topological order."""
    await get_current_user(request, db)
    result = EvidenceChainManager.replay(chain_id)
    if not result:
        return {"error": f"Evidence chain not found: {chain_id}"}
    return result


@router.get("/chains/{chain_id}/stats")
async def chain_stats(chain_id: str, request: Request, db=Depends(get_db)):
    """Get statistics for a chain."""
    await get_current_user(request, db)
    chain = EvidenceChainManager.load(chain_id)
    if not chain:
        return {"error": f"Evidence chain not found: {chain_id}"}
    return {"stats": chain.stats()}