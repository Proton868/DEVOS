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

Canonical boundary (Phase 4 complete):
  TaskRequest → WorkerRuntime.run(task=...) → BrainExecutionLoop → TaskResult
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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
        slug: str = None,
        goal: str = None,
        requester_identity=None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        on_step=None,
        tenant_id: Optional[str] = None,
        db=None,
        owner_id: Optional[str] = None,
        task=None,
    ):
        """Run a worker.

        Canonical: task=TaskRequest → returns (state, identity, TaskResult)
        Legacy: slug+goal → returns (state, identity, TaskResult) as well
                (3-tuple; existing 2-tuple callers should use [0],[1]).
        """
        from core.task_contract import TaskRequest, TaskResult, TaskStatus
        from core.task_registry import TaskRegistry

        req: Optional[TaskRequest] = None
        if task is not None:
            req = task if isinstance(task, TaskRequest) else TaskRequest.from_dict(task)
            slug = req.worker_slug
            goal = req.objective
        if not slug or goal is None:
            raise ValueError("WorkerRuntime.run requires slug+goal or task=")
        if requester_identity is None:
            raise ValueError("requester_identity is required")

        if req is None:
            req = TaskRequest(
                objective=goal,
                worker_slug=slug,
                metadata={"source": "legacy_slug_goal"},
            )

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
            tenant_id = (req.metadata or {}).get("tenant_id")
        if not tenant_id:
            raise WorkerTrustUnavailable(
                "tenant_id is required for worker execution (fail-closed trust resolution)"
            )

        persona_caps = resolve_worker_capabilities(persona.tools)
        if req.required_capabilities:
            # Narrow further to intersection of trust path + request (request is UCIP-adjacent filter)
            pass  # actual authority remains trust snapshot below

        owner = owner_id or getattr(requester_identity, "user_id", None) or "system"

        try:
            job = await enqueue(
                owner_id=str(owner),
                tenant_id=tenant_id,
                job_type="worker_run",
                payload={
                    "worker": slug,
                    "goal": goal[:2000],
                    "task_id": req.task_id,
                    "execution_id": req.execution_id,
                    "attempt": req.attempt,
                },
                actor_id=getattr(requester_identity, "agent_id", None),
                priority=50,
            )
            job_id = job.id
        except Exception as e:
            logger.error("[workers] cannot enqueue execution job: %s", e)
            raise WorkerTrustUnavailable(f"execution job create failed: {e}") from e

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
        req.agent_id = getattr(delegated_identity, "agent_id", None) or req.agent_id

        # Durable request before execution
        try:
            reg = TaskRegistry()
            reg.save_request(req)
            if req.parent_task_id:
                reg.link_child(req.parent_task_id, req.task_id)
        except Exception as e:
            logger.debug("task registry save_request failed: %s", e)

        from core.loop import BrainExecutionLoop, BRAIN_SYSTEM_PROMPT
        from brain.agents import build_agent_system_prompt
        import inspect

        full_prompt = build_agent_system_prompt(persona, BRAIN_SYSTEM_PROMPT)
        loop_kwargs = dict(
            user_id=requester_identity.user_id,
            session_id=requester_identity.session_id,
            provider=provider,
            model=model,
            agent_identity=delegated_identity,
            persona_prompt=full_prompt,
            on_step=on_step,
        )
        sig = inspect.signature(BrainExecutionLoop.__init__)
        for k, v in {
            "parent_loop_id": req.parent_loop_id,
            "root_loop_id": req.root_loop_id,
            "worker_slug": slug,
            "task_id": req.task_id,
            "execution_id": req.execution_id,
            "attempt": req.attempt,
        }.items():
            if k in sig.parameters:
                loop_kwargs[k] = v

        loop = BrainExecutionLoop(**loop_kwargs)
        started = datetime.now(timezone.utc).isoformat()
        try:
            state = await loop.run(goal)
        except Exception as e:
            await complete(job_id, status="failed", error=str(e), result={"trust_snapshot": snap.to_dict()})
            result = TaskResult(
                task_id=req.task_id,
                execution_id=req.execution_id,
                worker_slug=slug,
                status=TaskStatus.FAILED,
                agent_id=req.agent_id,
                errors=[str(e)],
                failure_reason=str(e),
                parent_task_id=req.parent_task_id,
                parent_loop_id=req.parent_loop_id,
                root_loop_id=req.root_loop_id,
                attempt=req.attempt,
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                TaskRegistry().save_result(result)
            except Exception:
                pass
            raise

        await complete(
            job_id,
            status="succeeded" if getattr(state, "succeeded", False) else "failed",
            result={
                "worker": slug,
                "trust_snapshot": snap.to_dict(),
                "decision": str(getattr(state, "decision", "")),
                "task_id": req.task_id,
                "execution_id": req.execution_id,
            },
        )
        state._devos_execution_job_id = job_id  # type: ignore[attr-defined]
        state._devos_trust_snapshot = snap.to_dict()  # type: ignore[attr-defined]

        # Map state → TaskResult (prefer from_loop_state if available)
        if hasattr(TaskResult, "from_loop_state"):
            try:
                result = TaskResult.from_loop_state(req, state, agent_id=req.agent_id)
            except Exception:
                result = None
        else:
            result = None
        if result is None:
            decision = str(getattr(state, "decision", ""))
            if getattr(state, "cancel_requested", False) or "cancel" in decision.lower():
                status = TaskStatus.CANCELLED
            elif getattr(state, "succeeded", False) or "complete" in decision:
                status = TaskStatus.SUCCEEDED
            else:
                status = TaskStatus.FAILED
            result = TaskResult(
                task_id=req.task_id,
                execution_id=req.execution_id,
                worker_slug=slug,
                status=status,
                agent_id=req.agent_id,
                output=getattr(state, "final_answer", None),
                parent_task_id=req.parent_task_id,
                parent_loop_id=req.parent_loop_id,
                root_loop_id=req.root_loop_id,
                attempt=req.attempt,
                loop_id=getattr(state, "id", None),
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
                decision=decision,
            )
        result.started_at = result.started_at or started
        result.loop_id = result.loop_id or getattr(state, "id", None)
        try:
            TaskRegistry().save_result(result)
        except Exception as e:
            logger.debug("task registry save_result failed: %s", e)

        return state, delegated_identity, result
