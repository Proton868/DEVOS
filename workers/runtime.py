"""
Workers — Runtime.

Closes the Stage 3 gap: `brain/agents/__init__.py`'s AGENT_LIBRARY has 16
real persona definitions (ported from msitarzewski/agency-agents, MIT)
sitting as pure data — nothing in the codebase invoked them before this
module. This is the wrapper that turns a persona into an actually-runnable
Worker:

  1. Maps the persona's declared `tools` (already named after the exact
     ACTION_TO_CAP keys in governance/ucip.py — no separate mapping table
     needed) to real UCIP capabilities.
  2. Delegates a properly narrowed child identity from the requesting
     user's identity via AgentIdentity.delegate() (built in Session 3,
     had no real caller until now — this is its first one).
  3. Runs the Worker's goal through the existing BrainExecutionLoop, with
     the persona's system prompt composed into the loop's context and the
     delegated identity governing what it's actually allowed to do.

A Worker can only ever do what BOTH its persona declares AND what the
requesting user's own identity already permits — delegation narrows,
per Session 3's design, so a low-trust user can't summon a Worker to
exceed their own capabilities just by asking nicely.
"""
import logging
from typing import Optional

logger = logging.getLogger("devos.workers.runtime")


class UnknownWorkerError(Exception):
    pass


def resolve_worker_capabilities(tool_names: list[str]) -> set[str]:
    """Maps a persona's declared tool names to real UCIP capability
    strings. Silently drops any tool name that isn't a recognized action
    (logged, not raised) — a persona listing a not-yet-implemented tool
    shouldn't break every other capability it correctly declares."""
    from governance.ucip import ACTION_TO_CAP
    caps = set()
    for tool in tool_names:
        cap = ACTION_TO_CAP.get(tool)
        if cap:
            caps.add(cap)
        else:
            logger.warning(f"[workers] persona declares unknown tool '{tool}' — skipped, not a real UCIP action")
    return caps


class WorkerRuntime:
    async def run(self, slug: str, goal: str, requester_identity,
                  provider: Optional[str] = None, model: Optional[str] = None,
                  on_step=None):
        """requester_identity: the calling user's own AgentIdentity (built
        the same way api/routes/loop.py builds one) — the Worker's actual
        identity is delegated FROM this, never created fresh at full trust,
        so a Worker can never exceed what the requester who summoned it
        was already allowed to do."""
        from brain.agents import AGENT_LIBRARY
        persona = AGENT_LIBRARY.get(slug)
        if not persona:
            raise UnknownWorkerError(f"No worker persona registered for slug '{slug}'")

        worker_caps = resolve_worker_capabilities(persona.tools)
        delegated_identity = requester_identity.delegate(sub_caps=worker_caps)

        from core.loop import BrainExecutionLoop, BRAIN_SYSTEM_PROMPT
        from brain.agents import build_agent_system_prompt
        # build_agent_system_prompt already existed in brain/agents/__init__.py
        # (used by brain/builder.py) and produces a more complete composition
        # than reimplementing one here — includes the persona's declared
        # tools explicitly in the prompt text, not just governed via UCIP
        # capabilities. Reusing it rather than duplicating that logic.
        full_prompt = build_agent_system_prompt(persona, BRAIN_SYSTEM_PROMPT)

        loop = BrainExecutionLoop(
            user_id=requester_identity.user_id,
            session_id=requester_identity.session_id,
            provider=provider, model=model,
            agent_identity=delegated_identity,
            persona_prompt=full_prompt,
            on_step=on_step,
        )
        state = await loop.run(goal)
        return state, delegated_identity
