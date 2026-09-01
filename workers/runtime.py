"""
Workers — Runtime.

Fail-closed: WorkerTrustRecord must load successfully or execution is refused.
Human requester identity is delegated to a Worker identity narrowed by
persona tools ∩ requester caps ∩ competency-aware filter.
"""
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
    ):
        from brain.agents import AGENT_LIBRARY
        from governance.agency_evolution import (
            get_or_create_trust,
            filter_autonomous_caps,
            TrustLoadError,
        )
        from core.database import AsyncSessionLocal

        persona = AGENT_LIBRARY.get(slug)
        if not persona:
            raise UnknownWorkerError(f"No worker persona registered for slug '{slug}'")

        if not tenant_id:
            raise WorkerTrustUnavailable(
                "tenant_id is required for worker execution (fail-closed trust resolution)"
            )

        worker_caps = resolve_worker_capabilities(persona.tools)

        # FAIL CLOSED: trust must load; no silent fallback to full persona caps
        try:
            async with AsyncSessionLocal() as tdb:
                row = await get_or_create_trust(tdb, tenant_id, slug)
                worker_caps = filter_autonomous_caps(worker_caps, row)
        except TrustLoadError as e:
            logger.error("[workers] trust load failed closed for %s: %s", slug, e)
            raise WorkerTrustUnavailable(str(e)) from e
        except WorkerTrustUnavailable:
            raise
        except Exception as e:
            logger.error("[workers] trust resolution error (fail closed): %s", e)
            raise WorkerTrustUnavailable(f"trust resolution failed: {e}") from e

        if not worker_caps:
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
        state = await loop.run(goal)
        return state, delegated_identity
