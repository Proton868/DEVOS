"""Durable multi-page Web Intelligence crawl APIs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user, get_db, ensure_personal_tenant
from execution.web_intel.url_norm import normalize_url
from execution.web_intel.safety import is_url_allowed
from execution.web_intel.crawler import start_crawl, cancel_crawl, resume_crawl, build_report, collect_evidence
from execution.web_intel.store import get_crawl, list_crawls, list_pages, list_events
from observability.tracing import new_trace, set_current_trace, start_span

router = APIRouter(prefix="/api/web", tags=["web-crawls"])


class CrawlCreate(BaseModel):
    root_url: str = Field(..., min_length=3, max_length=2000)
    project_id: Optional[str] = None
    persona_id: str = "nuha"
    mission_id: Optional[str] = None
    max_depth: int = Field(2, ge=0, le=5)
    max_pages: int = Field(20, ge=1, le=200)
    max_bytes: int = Field(3_000_000, ge=10_000, le=50_000_000)
    max_requests: int = Field(40, ge=1, le=500)
    same_domain_only: bool = True
    include_subdomains: bool = False
    obey_robots: bool = True
    sitemap_enabled: bool = True


def _ucip_web(user_id: str, url: str) -> None:
    try:
        from governance.ucip import UCIPGateway, AgentIdentity, TrustLevel
        import uuid
        agent = AgentIdentity(
            agent_id=f"ucip:webcrawl:{uuid.uuid4().hex[:10]}",
            user_id=user_id,
            session_id=uuid.uuid4().hex[:8],
            trust_level=TrustLevel.ASSISTANT,
            capabilities={"ucip:search.web", "ucip:api.call"},
        )
        decision = UCIPGateway(agent).request(
            "search_web", action_input=url[:500], context={"web_intelligence": True, "crawl": True},
        )
        if not decision.approved():
            raise HTTPException(403, f"UCIP denied: {getattr(decision, 'reason', 'denied')}")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "UCIP unavailable for crawl")


@router.post("/crawls")
async def create_crawl(body: CrawlCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    nu = normalize_url(body.root_url)
    ok, reason = is_url_allowed(nu)
    if not ok:
        raise HTTPException(400, f"URL blocked: {reason}")
    _ucip_web(str(user.id), nu)
    trace = new_trace()
    set_current_trace(trace)
    with start_span("web.crawl.start", kind="client", attributes={"root": nu[:80]}):
        crawl = start_crawl(
            user_id=str(user.id),
            root_url=body.root_url,
            project_id=body.project_id,
            persona_id=body.persona_id,
            mission_id=body.mission_id,
            max_depth=body.max_depth,
            max_pages=body.max_pages,
            max_bytes=body.max_bytes,
            max_requests=body.max_requests,
            same_domain_only=body.same_domain_only,
            include_subdomains=body.include_subdomains,
            obey_robots=body.obey_robots,
            sitemap_enabled=body.sitemap_enabled,
            trace_id=trace.trace_id,
        )
    return crawl


@router.get("/crawls")
async def crawls_list(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    return {"crawls": list_crawls(str(user.id))}


@router.get("/crawls/{crawl_id}")
async def crawl_get(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    return c


@router.post("/crawls/{crawl_id}/cancel")
async def crawl_cancel(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    return cancel_crawl(crawl_id)


@router.post("/crawls/{crawl_id}/resume")
async def crawl_resume(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    _ucip_web(str(user.id), c.get("normalized_root_url") or c.get("root_url") or "")
    return resume_crawl(crawl_id)


@router.get("/crawls/{crawl_id}/pages")
async def crawl_pages(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    return {"pages": list_pages(crawl_id)}


@router.get("/crawls/{crawl_id}/events")
async def crawl_events(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    return {"events": list_events(crawl_id)}


@router.get("/crawls/{crawl_id}/report")
async def crawl_report(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    return build_report(crawl_id)


@router.get("/crawls/{crawl_id}/evidence")
async def crawl_evidence(crawl_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    c = get_crawl(crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    return {"evidence": collect_evidence(crawl_id)}


class QueryReq(BaseModel):
    crawl_id: str
    question: str = Field(..., min_length=2, max_length=500)


@router.post("/query")
async def crawl_query(body: QueryReq, request: Request, db=Depends(get_db)):
    """Evidence-first query over completed crawl (no network)."""
    user = await get_current_user(request, db)
    c = get_crawl(body.crawl_id)
    if not c or c.get("user_id") != str(user.id):
        raise HTTPException(404, "crawl not found")
    q = body.question.lower()
    evidence = collect_evidence(body.crawl_id)
    hits = []
    for e in evidence:
        blob = f"{e.get('type','')} {e.get('value','')}".lower()
        if any(w in blob for w in q.split() if len(w) > 2):
            hits.append(e)
    return {"crawl_id": body.crawl_id, "question": body.question, "matches": hits[:50], "source": "evidence_only"}
