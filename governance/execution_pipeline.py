"""Unified execution pipeline for all DevOS run paths.

identity → UCI authorization → (worker trust snapshot) → isolation
        → evidence → learning (workers)

Legacy/alternate callers should go through helpers here so nothing bypasses
governance. Human terminal remains explicitly out of sandbox policy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Any, Callable, Awaitable

logger = logging.getLogger("devos.execution_pipeline")


@dataclass
class PipelineContext:
    """Authority + job context for one execution."""
    user_id: str
    tenant_id: str
    session_id: str
    actor_kind: str = "human"  # human | worker | system
    worker_id: Optional[str] = None
    execution_job_id: Optional[str] = None
    trust_snapshot: Optional[dict] = None
    agent_identity: Any = None
    identity_context: Any = None


async def resolve_human_identity(user, tenant_id: str, session_id: str):
    """Canonical human requester identity (never WORKER)."""
    from governance.identity_authority import identity_from_user
    from governance.ucip import TrustLevel
    ctx = identity_from_user(
        user.id,
        session_id,
        tenant_id=tenant_id,
        is_admin=bool(getattr(user, "is_admin", False)),
        trust=TrustLevel.OPERATOR,
    )
    return ctx, ctx.to_agent_identity()


async def begin_execution_job(
    *,
    owner_id: str,
    tenant_id: str,
    job_type: str,
    payload: dict,
    actor_id: Optional[str] = None,
) -> str:
    from workers.job_queue import enqueue
    job = await enqueue(
        owner_id=owner_id,
        tenant_id=tenant_id,
        job_type=job_type,
        payload=payload,
        actor_id=actor_id,
        priority=50,
    )
    return job.id


async def complete_execution_job(
    job_id: str, *, status: str, result: Optional[dict] = None, error: Optional[str] = None
):
    from workers.job_queue import complete
    await complete(job_id, status=status, result=result, error=error)


async def authorize_action(agent_identity, action: str, **kwargs):
    """UCI gate — same path BrainExecutionLoop uses."""
    from governance.ucip import UCIPGateway, BudgetPolicy
    gateway = UCIPGateway(agent_identity, BudgetPolicy())
    return gateway.request(action, **kwargs)


async def run_sandboxed_code(
    code: str,
    *,
    language: str = "python",
    allow_network: bool = False,
    timeout: int = 60,
    inject_secrets: Optional[dict] = None,
    run_id: Optional[str] = None,
):
    """Isolation path for non-HUMAN_TERMINAL code execution."""
    from governance.sandbox import SandboxedExecutor
    executor = SandboxedExecutor(
        max_cpu_seconds=min(30, timeout),
        max_memory_mb=256,
        max_output_bytes=512_000,
        allow_network=allow_network,
        max_file_size_mb=10,
    )
    return await executor.run(
        code=code,
        language=language,
        run_id=run_id,
        inject_secrets=inject_secrets or {},
        timeout=timeout,
    )


async def record_path_evidence(
    *,
    tenant_id: str,
    owner_id: str,
    goal: str,
    path: str,
    status: str,
    body: Optional[dict] = None,
    execution_job_id: Optional[str] = None,
):
    """Durable evidence for non-worker paths (loop, scripts, research)."""
    try:
        import hashlib, json
        from datetime import datetime, timezone
        from core.database import AsyncSessionLocal, EvidenceRecord, gen_id
        payload = {
            "path": path,
            "status": status,
            "body": body or {},
            "execution_job_id": execution_job_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        payload["hash"] = hashlib.sha256(raw.encode()).hexdigest()
        async with AsyncSessionLocal() as db:
            rec = EvidenceRecord(
                id=gen_id(),
                owner_id=owner_id,
                tenant_id=tenant_id,
                goal=goal[:2000],
                body=payload,
            )
            db.add(rec)
            await db.commit()
            return rec.id
    except Exception as e:
        logger.warning("path evidence write failed: %s", e)
        return None


async def run_brain_loop(
    *,
    user,
    tenant_id: str,
    session_id: str,
    goal: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    on_step=None,
    agent_identity=None,
    persona_prompt: Optional[str] = None,
    path: str = "brain_loop",
):
    """Canonical BrainExecutionLoop path with job + evidence."""
    from core.loop import BrainExecutionLoop

    ctx, identity = await resolve_human_identity(user, tenant_id, session_id)
    if agent_identity is not None:
        identity = agent_identity

    job_id = None
    try:
        job_id = await begin_execution_job(
            owner_id=user.id,
            tenant_id=tenant_id,
            job_type=path,
            payload={"goal": goal[:2000], "path": path},
            actor_id=getattr(identity, "agent_id", None),
        )
    except Exception as e:
        logger.warning("job enqueue failed (continuing with evidence-only): %s", e)

    loop = BrainExecutionLoop(
        user_id=user.id,
        session_id=session_id,
        provider=provider,
        model=model,
        agent_identity=identity,
        persona_prompt=persona_prompt,
        on_step=on_step,
    )
    try:
        state = await loop.run(goal)
    except Exception as e:
        if job_id:
            await complete_execution_job(job_id, status="failed", error=str(e))
        raise

    success = bool(getattr(state, "succeeded", False))
    if job_id:
        await complete_execution_job(
            job_id,
            status="succeeded" if success else "failed",
            result={"decision": str(getattr(state, "decision", ""))},
        )
    await record_path_evidence(
        tenant_id=tenant_id,
        owner_id=user.id,
        goal=goal,
        path=path,
        status="success" if success else "failed",
        body={"decision": str(getattr(state, "decision", "")), "iterations": getattr(state, "iteration", 0)},
        execution_job_id=job_id,
    )
    if job_id:
        state._devos_execution_job_id = job_id  # type: ignore
    return state, ctx, identity
