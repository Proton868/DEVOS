"""
Workers — Runtime.

Human requester identity is delegated to a Worker identity. Worker trust and
competency come from stored WorkerTrustRecord — never from client input.
"""
import logging
from typing import Optional

logger = logging.getLogger("devos.workers.runtime")


class UnknownWorkerError(Exception):
    pass


def resolve_worker_capabilities(tool_names: list[str]) -> set[str]:
    from governance.ucip import ACTION_TO_CAP
    caps = set()
    for tool in tool_names:
        cap = ACTION_TO_CAP.get(tool)
        if cap:
            caps.add(cap)
        else:
            logger.warning(
                "[workers] persona declares unknown tool '%s' — skipped", tool
            )
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
        """requester_identity: calling user's AgentIdentity (HUMAN).

        Worker identity is delegated and narrowed by:
          persona tools ∩ requester caps ∩ competency-aware autonomous set
        """
        from brain.agents import AGENT_LIBRARY
        persona = AGENT_LIBRARY.get(slug)
        if not persona:
            raise UnknownWorkerError(f"No worker persona registered for slug '{slug}'")

        worker_caps = resolve_worker_capabilities(persona.tools)

        # Narrow by earned competency when trust record available
        if tenant_id:
            try:
                from core.database import AsyncSessionLocal
                from governance.agency_evolution import (
                    get_or_create_trust,
                    filter_autonomous_caps,
                    trust_level_from_record,
                )
                async with AsyncSessionLocal() as tdb:
                    row = await get_or_create_trust(tdb, tenant_id, slug)
                    worker_caps = filter_autonomous_caps(worker_caps, row)
            except Exception as e:
                logger.warning("[workers] trust load failed: %s", e)

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
