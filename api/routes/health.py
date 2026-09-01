from fastapi import APIRouter
from core.config import settings

router = APIRouter()


@router.get("")
async def health():
    """Liveness/readiness probe (security-audit P4e). Actually checks the
    database connection instead of returning a static "ok" -- a process
    that's up but can't reach its DB should report unhealthy so an
    orchestrator (k8s, docker-compose healthcheck, etc.) can restart/avoid
    routing traffic to it. Never raises: DB failures are caught and reported
    as a degraded status with a 200 (some orchestrators only check the JSON
    body) plus an explicit "db": "error" field callers can key off of."""
    from memory.store import MemoryStore
    from core.database import engine
    from sqlalchemy import text

    db_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "memory": MemoryStore().backend,
        "providers": settings.available_providers,
        "tavily": settings.has_tavily,
    }
