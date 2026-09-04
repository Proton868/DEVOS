"""Web Intelligence API — public data only, UCIP-gated at tool layer."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user, get_db, ensure_personal_tenant
from execution.web_intel.provider import get_web_intel_provider
from execution.web_intel.safety import is_url_allowed, normalize_url
from observability.tracing import start_span, new_trace, set_current_trace

router = APIRouter(prefix="/api/web-intel", tags=["web-intel"])


class FetchReq(BaseModel):
    url: str = Field(..., min_length=3, max_length=2000)
    allowlist: Optional[list[str]] = None


@router.get("/health")
async def health(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    p = get_web_intel_provider()
    return {"provider": p.name, "configured": True}


@router.post("/fetch")
async def fetch_public(body: FetchReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    # Authorization: require web search capability via UCIP if available
    try:
        from governance.ucip import UCIPGateway, AgentIdentity, TrustLevel
        import uuid
        agent = AgentIdentity(
            agent_id=f"ucip:webintel:{uuid.uuid4().hex[:10]}",
            user_id=str(user.id),
            session_id=uuid.uuid4().hex[:8],
            trust_level=TrustLevel.ASSISTANT,
            capabilities={"ucip:search.web", "ucip:api.call"},
        )
        decision = UCIPGateway(agent).request(
            "search_web",
            action_input=body.url[:500],
            context={"web_intelligence": True, "url": normalize_url(body.url)[:200]},
        )
        if not decision.approved():
            raise HTTPException(403, f"UCIP denied: {getattr(decision, 'reason', 'denied')}")
    except HTTPException:
        raise
    except Exception:
        # Fail closed if UCIP cannot evaluate
        raise HTTPException(403, "UCIP unavailable for web intelligence")

    ok, reason = is_url_allowed(body.url, allowlist=body.allowlist)
    if not ok:
        raise HTTPException(400, f"URL blocked: {reason}")

    trace = new_trace()
    set_current_trace(trace)
    with start_span("web.intelligence.fetch", kind="client", attributes={"url_host": normalize_url(body.url)[:80]}):
        result = get_web_intel_provider().fetch_public(body.url, allowlist=body.allowlist)
        result.trace_id = trace.trace_id
    try:
        from execution.outbox import enqueue
        enqueue(
            "web.crawl.completed" if not result.error else "web.crawl.failed",
            aggregate_type="web_intel",
            aggregate_id=trace.trace_id,
            payload={"url": result.canonical_url or result.source_url, "error": result.error},
            trace_id=trace.trace_id,
            idempotency_key=f"web.fetch:{trace.trace_id}",
        )
    except Exception:
        pass
    return result.to_dict()
