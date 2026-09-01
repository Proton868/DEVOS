"""Unified execution pipeline for all DevOS run paths.

identity → UCI authorization → (worker trust snapshot) → isolation
        → evidence → learning (workers)

Default posture: FAIL CLOSED on job creation and evidence write failures
for durable/governed paths. Explicit allow_evidence_only / allow_missing_job
only for NON_DURABLE / HUMAN_ONLY / READ_ONLY classifications.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger("devos.execution_pipeline")


class PathClass(str, Enum):
    DURABLE = "durable"           # must have job + evidence
    NON_DURABLE = "non_durable"   # evidence-only ok if job fails
    HUMAN_ONLY = "human_only"     # terminal / explicit human
    READ_ONLY = "read_only"       # no execution side effects


class PipelineError(Exception):
    """Fail-closed pipeline failure."""


class JobCreationError(PipelineError):
    pass


class EvidenceWriteError(PipelineError):
    pass


@dataclass
class PipelineContext:
    user_id: str
    tenant_id: str
    session_id: str
    actor_kind: str = "human"
    worker_id: Optional[str] = None
    execution_job_id: Optional[str] = None
    trust_snapshot: Optional[dict] = None
    agent_identity: Any = None
    identity_context: Any = None
    path_class: PathClass = PathClass.DURABLE
    governance_state: str = "ok"  # ok | degraded


def network_allowed_for_capability(capability: Optional[str] = None) -> bool:
    """Network permission is derived from UCI capability metadata, never caller intent."""
    if not capability:
        return False
    try:
        from governance.capability_registry import CapabilityRegistry
        reg = CapabilityRegistry()
        desc = None
        if hasattr(reg, "get"):
            desc = reg.get(capability)
        if desc is None and hasattr(reg, "authorize_capability_slug"):
            # registry may only authorize, not return descriptor
            pass
        if desc is not None:
            return bool(getattr(desc, "requires_network", False))
    except Exception as e:
        logger.warning("capability registry lookup failed (deny network): %s", e)
    # Known network capabilities from UCIP taxonomy
    network_caps = {
        "ucip:network.outbound",
        "ucip:network.exfiltrate",
        "ucip:search.web",
        "ucip:api.call",
    }
    return capability in network_caps


async def resolve_human_identity(user, tenant_id: str, session_id: str):
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
    path_class: PathClass = PathClass.DURABLE,
    allow_missing_job: bool = False,
    idempotency_key: Optional[str] = None,
    request_id: Optional[str] = None,
    correlation: Optional[dict] = None,
) -> Optional[str]:
    """Create durable job. Fail-closed for DURABLE paths unless explicitly opted out."""
    try:
        from workers.job_queue import enqueue
        from governance.reliability import new_idempotency_key, new_request_id, check_quota_async
        # Abuse control before durable work is created
        q = await check_quota_async(tenant_id or owner_id, "max_jobs_per_hour")
        if not q.allowed:
            raise JobCreationError(q.reason)
        rid = request_id or new_request_id()
        ikey = idempotency_key
        if not ikey and path_class == PathClass.DURABLE:
            ikey = new_idempotency_key(
                tenant_id=tenant_id or "none",
                actor_id=owner_id,
                capability=job_type,
                operation=job_type,
                body={"payload_keys": sorted((payload or {}).keys())},
            )
        job = await enqueue(
            owner_id=owner_id,
            tenant_id=tenant_id,
            job_type=job_type,
            payload=payload,
            actor_id=actor_id,
            priority=50,
            idempotency_key=ikey,
            request_id=rid,
            correlation=correlation or {"request_id": rid},
        )
        return job.id
    except Exception as e:
        if allow_missing_job or path_class in (PathClass.NON_DURABLE, PathClass.READ_ONLY, PathClass.HUMAN_ONLY):
            logger.warning("job enqueue failed (allowed for %s): %s", path_class, e)
            return None
        raise JobCreationError(f"cannot create execution job: {e}") from e


async def complete_execution_job(
    job_id: Optional[str], *, status: str, result: Optional[dict] = None, error: Optional[str] = None
):
    if not job_id:
        return
    from workers.job_queue import complete
    await complete(job_id, status=status, result=result, error=error)


async def authorize_action(agent_identity, action: str, **kwargs):
    from governance.ucip import UCIPGateway, BudgetPolicy
    gateway = UCIPGateway(agent_identity, BudgetPolicy())
    return gateway.request(action, **kwargs)


async def run_sandboxed_code(
    code: str,
    *,
    language: str = "python",
    capability: Optional[str] = None,
    allow_network: Optional[bool] = None,  # ignored if capability set; never authoritative
    timeout: int = 60,
    inject_secrets: Optional[dict] = None,
    run_id: Optional[str] = None,
):
    """Isolation path. Network is derived from UCI capability, not caller."""
    net = network_allowed_for_capability(capability)
    if allow_network is True and not net:
        logger.warning(
            "caller requested allow_network=True but capability %r does not authorize network — denying",
            capability,
        )
        net = False
    from governance.sandbox import SandboxedExecutor
    executor = SandboxedExecutor(
        max_cpu_seconds=min(30, timeout),
        max_memory_mb=256,
        max_output_bytes=512_000,
        allow_network=net,
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
    path_class: PathClass = PathClass.DURABLE,
    require_evidence: bool = True,
) -> Optional[str]:
    """Write durable evidence. Fail-closed for DURABLE paths when require_evidence."""
    try:
        import hashlib
        import json
        from datetime import datetime, timezone
        from core.database import AsyncSessionLocal, EvidenceRecord, gen_id

        from governance.reliability import scrub_secrets
        payload = {
            "path": path,
            "status": status,
            "body": scrub_secrets(body or {}),
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
        if require_evidence and path_class == PathClass.DURABLE:
            raise EvidenceWriteError(f"evidence write failed: {e}") from e
        logger.warning("evidence write failed (degraded allowed): %s", e)
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
    path_class: PathClass = PathClass.DURABLE,
    allow_evidence_only: bool = False,
    tenant_id_for_workers: Optional[str] = None,
):
    """Canonical BrainExecutionLoop path with fail-closed job + evidence."""
    from core.loop import BrainExecutionLoop

    if allow_evidence_only:
        path_class = PathClass.NON_DURABLE

    ctx, identity = await resolve_human_identity(user, tenant_id, session_id)
    if agent_identity is not None:
        identity = agent_identity

    job_id = await begin_execution_job(
        owner_id=user.id,
        tenant_id=tenant_id,
        job_type=path,
        payload={"goal": goal[:2000], "path": path},
        actor_id=getattr(identity, "agent_id", None),
        path_class=path_class,
        allow_missing_job=path_class != PathClass.DURABLE,
    )

    loop = BrainExecutionLoop(
        user_id=user.id,
        session_id=session_id,
        provider=provider,
        model=model,
        agent_identity=identity,
        persona_prompt=persona_prompt,
        on_step=on_step,
        tenant_id=tenant_id_for_workers or tenant_id,
    )
    try:
        state = await loop.run(goal)
    except Exception as e:
        await complete_execution_job(job_id, status="failed", error=str(e))
        raise

    success = bool(getattr(state, "succeeded", False))
    await complete_execution_job(
        job_id,
        status="succeeded" if success else "failed",
        result={"decision": str(getattr(state, "decision", ""))},
    )

    governance_state = "ok"
    try:
        evidence_id = await record_path_evidence(
            tenant_id=tenant_id,
            owner_id=user.id,
            goal=goal,
            path=path,
            status="success" if success else "failed",
            body={
                "decision": str(getattr(state, "decision", "")),
                "iterations": getattr(state, "iteration", 0),
            },
            execution_job_id=job_id,
            path_class=path_class,
            require_evidence=(path_class == PathClass.DURABLE),
        )
        if evidence_id is None and path_class == PathClass.DURABLE:
            governance_state = "degraded"
            state._devos_promotion_credit = False  # type: ignore
    except EvidenceWriteError:
        # Execution may have succeeded; governance is DEGRADED — no promotion credit
        governance_state = "degraded"
        logger.error("evidence unavailable after execution — governance DEGRADED (no promotion credit)")
        state._devos_governance_state = "degraded"  # type: ignore
        state._devos_promotion_credit = False  # type: ignore

    if job_id:
        state._devos_execution_job_id = job_id  # type: ignore
    state._devos_governance_state = governance_state  # type: ignore
    return state, ctx, identity
