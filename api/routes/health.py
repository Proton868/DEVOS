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

    # Mission stack probes (no secrets)
    mission = {
        "agent_runtime": "unknown",
        "fake_runtime_env": False,
        "orchestration_store": "unknown",
        "ucip": "unknown",
        "workspace": "unknown",
    }
    try:
        from brain.agent_runtime import AgentRuntime
        mission["agent_runtime"] = "import_ok"
    except Exception as e:
        mission["agent_runtime"] = f"unavailable:{type(e).__name__}"
    try:
        import os as _os
        mission["fake_runtime_env"] = _os.environ.get("DEVOS_ORCH_FAKE_RUNTIME") == "1"
    except Exception:
        mission["fake_runtime_env"] = False
    try:
        from brain.orchestration_store import persist_plan
        mission["orchestration_store"] = "ok"
    except Exception as e:
        mission["orchestration_store"] = f"error:{type(e).__name__}"
    try:
        from governance.ucip import ALWAYS_BLOCKED_CAPS
        mission["ucip"] = "ok" if ALWAYS_BLOCKED_CAPS is not None else "missing"
    except Exception as e:
        mission["ucip"] = f"error:{type(e).__name__}"
    try:
        from execution.files import FileService
        mission["workspace"] = "import_ok"
    except Exception as e:
        mission["workspace"] = f"unavailable:{type(e).__name__}"

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
        "mission_runtime": mission,
    }
