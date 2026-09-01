"""
Workers — Runtime.

Consistency model:
  create ExecutionJob
       ↓
  load trust → TrustSnapshot (immutable for this job)
       ↓
  execute under snapshot.permitted_caps
       ↓
  evaluate + durable evidence (linked to job id)
       ↓
  update trust

Fail-closed: any trust/job failure refuses execution.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("devos.workers.runtime")


class UnknownWorkerError(Exception):
    pass


class WorkerTrustUnavailable(Exception):
    """Trust store could not be resolved — execution must not proceed."""


def resolve_worker_capabilities(tool_names: list[str]) -> set[str]:
    from governance.ucip import ACTION_TO_CAP
    caps = set()
    for tool in tool_names:
        cap = ACTION_TO_CAP.get(tool)
        if cap:
            caps.add(cap)
        else:
            logger.warning("[workers] persona declares unknown tool '%s' — skipped", tool)
    return caps


class WorkerRuntime:
    async def run(
        self,
        slug: str,
        goal: str,
        requester_identity,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        on_step=None,
        tenant_id: Optional[str] = None,
        db=None,
        owner_id: Optional[str] = None,
    ):
        from brain.agents import AGENT_LIBRARY
        from governance.agency_evolution import (
            snapshot_trust,
            TrustLoadError,
        )
        from core.database import AsyncSessionLocal
        from workers.job_queue import enqueue, complete

        persona = AGENT_LIBRARY.get(slug)
        if not persona:
            raise UnknownWorkerError(f"No worker persona registered for slug '{slug}'")

        if not tenant_id:
            raise WorkerTrustUnavailable(
                "tenant_id is required for worker execution (fail-closed trust resolution)"
            )

        persona_caps = resolve_worker_capabilities(persona.tools)
        owner = owner_id or getattr(requester_identity, "user_id", None) or "system"

        # 1) Durable job ties the whole operation together
        try:
            job = await enqueue(
                owner_id=str(owner),
                tenant_id=tenant_id,
                job_type="worker_run",
                payload={"worker": slug, "goal": goal[:2000]},
                actor_id=getattr(requester_identity, "agent_id", None),
                priority=50,
            )
            job_id = job.id
        except Exception as e:
            logger.error("[workers] cannot enqueue execution job: %s", e)
            raise WorkerTrustUnavailable(f"execution job create failed: {e}") from e

        # 2) Snapshot trust authority for this job (fail closed)
        try:
            async with AsyncSessionLocal() as tdb:
                snap = await snapshot_trust(
                    tdb, tenant_id, slug,
                    execution_job_id=job_id,
                    persona_caps=persona_caps,
                )
        except TrustLoadError as e:
            await complete(job_id, status="failed", error=f"trust: {e}")
            raise WorkerTrustUnavailable(str(e)) from e
        except Exception as e:
            await complete(job_id, status="failed", error=f"trust: {e}")
            raise WorkerTrustUnavailable(f"trust snapshot failed: {e}") from e

        worker_caps = set(snap.permitted_caps)
        if not worker_caps:
            await complete(job_id, status="failed", error="no permitted capabilities")
            raise WorkerTrustUnavailable(
                f"worker '{slug}' has no permitted capabilities under current trust/competency"
            )

        delegated_identity = requester_identity.delegate(sub_caps=worker_caps)

        from core.loop import BrainExecutionLoop, BRAIN_SYSTEM_PROMPT
        from brain.agents import build_agent_system_prompt

        full_prompt = build_agent_system_prompt(persona, BRAIN_SYSTEM_PROMPT)
        loop = BrainExecutionLoop(
            user_id=requester_identity.user_id,
            session_id=requester_identity.session_id,
            provider=provider,
            model=model,
            agent_identity=delegated_identity,
            persona_prompt=full_prompt,
            on_step=on_step,
        )

        try:
            state = await loop.run(goal)
        except Exception as e:
            await complete(job_id, status="failed", error=str(e), result={"trust_snapshot": snap.to_dict()})
            raise

        await complete(
            job_id,
            status="succeeded" if getattr(state, "succeeded", False) else "failed",
            result={
                "worker": slug,
                "trust_snapshot": snap.to_dict(),
                "decision": str(getattr(state, "decision", "")),
            },
        )
        # Attach snapshot + job id for the route's learning phase
        state._devos_execution_job_id = job_id  # type: ignore[attr-defined]
        state._devos_trust_snapshot = snap.to_dict()  # type: ignore[attr-defined]
        return state, delegated_identity
