from fastapi import APIRouter
from core.config import settings

router = APIRouter()


@router.get("")
async def health():
    """Liveness/readiness: DB + optional queue recovery probe."""
    from memory.store import MemoryStore
    from core.database import engine
    from sqlalchemy import text

    db_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    recovered = 0
    try:
        from workers.job_queue import recover_stale_leases
        recovered = await recover_stale_leases()
    except Exception:
        recovered = -1

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "memory": MemoryStore().backend,
        "providers": settings.available_providers,
        "tavily": settings.has_tavily,
        "stale_jobs_recovered": recovered,
        "governance": "v1-frozen",
    }
