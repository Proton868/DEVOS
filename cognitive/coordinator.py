"""
Cognitive System — Coordinator.

Closes the last flagged Stage 3 gap: Session 7's GoalDecomposer produces a
real subtask DAG, and Session 8's WorkerRuntime can run a single Worker
under a properly delegated identity — but nothing connected the two.
Without this module, a decomposed plan was still worked through
sequentially by one generalist Brain (Session 7's own "known follow-up"
noted this explicitly). This is the actual multi-worker coordination piece:
each subtask gets routed to whichever Worker persona best matches it (via
brain.agents.get_best_agent_for_goal, which already existed and was only
used by brain/builder.py — same "real infrastructure, no caller yet"
pattern as AGENT_LIBRARY and AgentIdentity.delegate() before their first
real use), and subtasks run in dependency order, with subtasks that have no
unmet dependencies between each other run concurrently.

Scope note: this dispatches subtasks from ONE decomposed goal to potentially
several Workers. It is not yet Worker-to-Worker delegation (a Worker's own
task spawning a further-delegated sub-Worker) — that's a smaller, separate
extension of the same delegation_chain mechanism, not built here.
"""
import asyncio
import logging

logger = logging.getLogger("devos.cognitive.coordinator")


class SubtaskResult:
    def __init__(self, subtask_id: str, worker_slug: str, success: bool,
                 output: str, delegation_chain: list[str],
                 confidence: float = None, parse_failed: bool = False):
        self.subtask_id = subtask_id
        self.worker_slug = worker_slug
        self.success = success
        self.output = output
        self.delegation_chain = delegation_chain
        self.confidence = confidence
        self.parse_failed = parse_failed

    def to_dict(self) -> dict:
        return {"subtask_id": self.subtask_id, "worker_slug": self.worker_slug,
                "success": self.success, "output": self.output,
                "delegation_chain": self.delegation_chain,
                "confidence": self.confidence,
                "parse_failed": self.parse_failed}


class Coordinator:
    async def run_plan(self, subtasks: list, requester_identity,
                       provider=None, model=None, on_subtask_done=None) -> list[SubtaskResult]:
        """subtasks: cognitive.decomposer.Subtask list (already validated —
        no cycles, no dangling deps — by GoalDecomposer itself).
        Runs subtasks in dependency-respecting waves: everything with no
        unmet dependencies runs concurrently, then the next wave, etc. Each
        subtask is dispatched to whichever Worker persona best matches its
        description; if none matches well enough, it falls back to the
        generalist Brain rather than forcing a bad persona fit."""
        from brain.agents import get_best_agent_for_goal
        from workers.runtime import WorkerRuntime
        from core.loop import BrainExecutionLoop

        remaining = {t.id: t for t in subtasks}
        done: dict[str, SubtaskResult] = {}

        while remaining:
            ready_ids = [tid for tid, t in remaining.items()
                        if all(dep in done for dep in t.depends_on)]
            if not ready_ids:
                # Shouldn't happen if GoalDecomposer's DAG validation ran,
                # but defend anyway rather than hanging forever on a plan
                # with an undetected cycle or an externally-injected bad dep.
                stuck = list(remaining.keys())
                logger.error(f"[coordinator] no ready subtasks but {stuck} remain — breaking to avoid a hang")
                for tid in stuck:
                    done[tid] = SubtaskResult(tid, worker_slug="none", success=False,
                                              output="blocked: unresolvable dependency", delegation_chain=[])
                break

            async def run_one(subtask):
                persona = get_best_agent_for_goal(subtask.description)
                try:
                    if persona:
                        state, delegated = await WorkerRuntime().run(
                            persona.slug, subtask.description, requester_identity,
                            provider=provider, model=model,
                        )
                        result = SubtaskResult(subtask.id, persona.slug,
                                               success=(state.decision == "complete"),
                                               output=state.final_answer,
                                               delegation_chain=delegated.delegation_chain + [delegated.agent_id])
                    else:
                        # No persona is a good fit — fall back to the
                        # generalist Brain under the requester's own
                        # identity rather than forcing a mismatched Worker.
                        loop = BrainExecutionLoop(
                            user_id=requester_identity.user_id, session_id=requester_identity.session_id,
                            provider=provider, model=model, agent_identity=requester_identity,
                        )
                        state = await loop.run(subtask.description)
                        result = SubtaskResult(subtask.id, worker_slug="generalist-brain",
                                               success=(state.decision == "complete"),
                                               output=state.final_answer,
                                               delegation_chain=[requester_identity.agent_id])
                except Exception as e:
                    logger.warning(f"[coordinator] subtask {subtask.id} failed: {e}")
                    result = SubtaskResult(subtask.id, worker_slug=persona.slug if persona else "none",
                                           success=False, output=f"error: {e}", delegation_chain=[])

                # Reflect on the subtask result to get a confidence score
                try:
                    from cognitive.reflector import Reflector
                    from brain.llm import BrainLLM
                    ref_brain = BrainLLM(provider, model, user_id=requester_identity.user_id)
                    reflector = Reflector()
                    reflection = await reflector.reflect(
                        subtask.description, result.output, ref_brain,
                    )
                    result.confidence = reflection.confidence
                    result.parse_failed = reflection.parse_failed
                except Exception as ref_err:
                    logger.warning(f"[coordinator] reflection failed for {subtask.id}: {ref_err}")
                    result.confidence = None
                    result.parse_failed = True
                subtask.status = "done" if result.success else "failed"
                if on_subtask_done:
                    await on_subtask_done(result)
                return result

            wave_results = await asyncio.gather(*[run_one(remaining[tid]) for tid in ready_ids])
            for r in wave_results:
                done[r.subtask_id] = r
                del remaining[r.subtask_id]

        # Return in original subtask order, not completion order, so callers
        # get a stable, predictable result list regardless of concurrency.
        return [done[t.id] for t in subtasks]
