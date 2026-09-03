from fastapi import APIRouter
from core.config import settings

router = APIRouter()


@router.get("/isolation")
async def isolation_status():
    """Operator diagnostics for OS sandbox strength (no secrets/paths)."""
    from execution.isolation import detect_backends
    info = detect_backends()
    if not info.get("suitable_for_untrusted_code"):
        info["warning"] = (
            "No strong/restricted isolation backend for untrusted code. "
            "Set DEVOS_USE_DOCKER_SANDBOX=1 or install bubblewrap/firejail. "
            "DEVOS_ALLOW_DEGRADED_ISOLATION is for local development only."
        )
    return info


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
    isolation = {}
    try:
        from execution.isolation import detect_backends
        isolation = detect_backends()
    except Exception:
        isolation = {"available": False}

    return {
        "status": overall,
        "db": db_status,
        "memory": MemoryStore().backend,
        "providers": settings.available_providers,
        "tavily": settings.has_tavily,
        "stale_jobs_recovered": recovered,
        "governance": "v1-frozen",
        "isolation": {
            "backend": isolation.get("backend"),
            "strength": isolation.get("strength"),
            "suitable_for_untrusted_code": isolation.get("suitable_for_untrusted_code"),
        },
    }
