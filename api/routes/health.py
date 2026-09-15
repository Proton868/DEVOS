"""Health — non-secret backend identity for live acceptance."""
from __future__ import annotations

from fastapi import APIRouter

from core.config import settings

router = APIRouter(tags=["health"])


def _db_backend() -> str:
    try:
        from core.database import engine

        name = (engine.dialect.name or "").lower()
        if name in ("postgresql", "postgres"):
            return "postgres"
        if name.startswith("sqlite"):
            return "sqlite"
        return name or "unknown"
    except Exception:
        return "unknown"


@router.get("/health")
@router.get("/api/health")
async def health():
    from memory.store import MemoryStore
    from core.database import engine
    from sqlalchemy import text

    db_status = "ok"
    db_backend = _db_backend()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {type(e).__name__}"

    # Memory backend (must match Postgres when REQUIRE_POSTGRES)
    memory_status = "ok"
    memory_backend = "uninitialized"
    try:
        store = MemoryStore()
        await store.init()
        memory_backend = store.backend
        if bool(getattr(settings, "REQUIRE_POSTGRES", True)) and memory_backend not in (
            "postgres",
            "postgresql",
        ):
            memory_status = "error:sqlite_forbidden"
            memory_backend = memory_backend or "sqlite"
    except Exception as e:
        memory_status = f"error:{type(e).__name__}"
        memory_backend = "error"

    recovered = 0
    try:
        from workers.job_queue import recover_stale_leases

        recovered = await recover_stale_leases()
    except Exception:
        recovered = -1

    require_pg = bool(getattr(settings, "REQUIRE_POSTGRES", True))
    overall = "ok"
    if db_status != "ok":
        overall = "degraded"
    if require_pg and db_backend != "postgres":
        overall = "fail"
        db_status = "error:sqlite_or_non_postgres"
    if require_pg and memory_backend not in ("postgres", "postgresql"):
        overall = "fail"
    if memory_status != "ok" and require_pg:
        overall = "fail"

    isolation = {}
    try:
        from execution.isolation import detect_backends

        isolation = detect_backends()
    except Exception:
        isolation = {"available": False}

    mission = {
        "agent_runtime": "unknown",
        "fake_runtime_env": False,
        "orchestration_store": "unknown",
        "ucip": "unknown",
        "workspace": "unknown",
    }
    try:
        from brain.agent_runtime import AgentRuntime  # noqa: F401

        mission["agent_runtime"] = "import_ok"
    except Exception as e:
        mission["agent_runtime"] = f"unavailable:{type(e).__name__}"
    try:
        import os as _os

        mission["fake_runtime_env"] = _os.environ.get("DEVOS_ORCH_FAKE_RUNTIME") == "1"
    except Exception:
        mission["fake_runtime_env"] = False
    try:
        from brain.orchestration_store import persist_plan  # noqa: F401

        mission["orchestration_store"] = "ok"
    except Exception as e:
        mission["orchestration_store"] = f"error:{type(e).__name__}"
    try:
        from governance.ucip import ALWAYS_BLOCKED_CAPS

        mission["ucip"] = "ok" if ALWAYS_BLOCKED_CAPS is not None else "missing"
    except Exception as e:
        mission["ucip"] = f"error:{type(e).__name__}"
    try:
        from execution.files import FileService  # noqa: F401

        mission["workspace"] = "import_ok"
    except Exception as e:
        mission["workspace"] = f"unavailable:{type(e).__name__}"


    # Subsystem backends (derived — never hardcode)
    try:
        from core.sync_session import store_backend
        _sb = store_backend()
    except Exception:
        _sb = "unknown"
    execution_store_backend = _sb
    outbox_backend = _sb
    saga_backend = _sb
    audit_backend = _sb
    if require_pg and _sb != "postgres":
        overall = "fail"

    return {
        "service": "devos",
        "status": overall,
        "db": db_status,
        "db_backend": db_backend,
        # Back-compat field: prefer memory_backend for acceptance
        "memory": memory_status if memory_status == "ok" else memory_status,
        "memory_backend": memory_backend,
        "providers": list(settings.available_providers),
        "default_provider": getattr(settings, "DEFAULT_PROVIDER", None),
        "tavily": settings.has_tavily,
        "stale_jobs_recovered": recovered,
        "governance": "v1-frozen",
        "isolation": {
            "backend": isolation.get("backend"),
            "strength": isolation.get("strength"),
            "suitable_for_untrusted_code": isolation.get("suitable_for_untrusted_code"),
        },
        "mission_runtime": mission,
        "execution_store_backend": execution_store_backend,
        "outbox_backend": outbox_backend,
        "saga_backend": saga_backend,
        "audit_backend": audit_backend,
    }
