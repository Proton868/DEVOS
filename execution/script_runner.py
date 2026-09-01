"""Centralized script execution + recording.

Every trigger path (manual run, scheduler, webhook, script-chaining) used to
duplicate its own copy of "load script -> load secrets -> pick venv -> run ->
record ScriptRun" with subtle differences. That made retry policy (G9),
notifications (G9), and chaining (G8) impossible to apply consistently --
each caller would need its own copy of the same logic.

This module is the single place that:
  1. Runs a script, retrying per its `retry_policy` (none/once/twice).
  2. Records a ScriptRun row for the final attempt.
  3. Publishes a `script.success` / `script.failure` event on the owner's
     EventBus topic when notify_on_success/notify_on_failure is enabled,
     using the exact same publish pattern governance/hitl.py already uses.
  4. Triggers any enabled ScriptChain children whose `condition` matches
     the run's outcome, recursively -- giving Flow basic conditional
     branching without a full workflow-graph engine.

CRITICAL: Scheduled and webhook-triggered script executions now run in the
hardened SandboxedExecutor (governance/sandbox.py) which enforces CPU time,
memory, file descriptor, and output limits, strips sensitive env vars, and
runs in an isolated temp directory. The manual "Run" button in the UI still
uses the unsandboxed ExecutionLayer (execution/runner.py) for backward
compatibility with existing user workflows that may need broader access.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select

logger = logging.getLogger("devos.script_runner")

RETRY_ATTEMPTS = {"none": 1, "once": 2, "twice": 3}


async def run_and_record(script_id: str, trigger: str = "manual", _depth: int = 0) -> dict:
    """Run a script by id end-to-end: retries, records a ScriptRun, notifies,
    and fires chained children. Safe to call from BackgroundTasks, APScheduler
    jobs, or an unauthenticated webhook handler alike -- it opens its own DB
    sessions throughout rather than relying on a request-scoped session.

    `_depth` guards against runaway/cyclical chains (A -> B -> A ...); chains
    deeper than 10 hops are silently stopped rather than recursing forever.
    """
    from core.database import AsyncSessionLocal, Script, ScriptRun, ScriptChain
    from governance.sandbox import SandboxedExecutor
    from governance.secrets_vault import get_user_secrets_dict

    if _depth > 10:
        logger.warning(f"Script chain too deep (>10), stopping at {script_id}")
        return {"status": "error", "error": "chain depth exceeded"}

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Script).where(Script.id == script_id))
        s = r.scalar_one_or_none()
        if not s:
            return {"status": "error", "error": "script not found"}
        user_secrets = await get_user_secrets_dict(db, s.owner_id)

    # Use SandboxedExecutor for scheduled/webhook runs (hardened), but keep
    # ExecutionLayer for manual runs (backward compatibility with existing
    # user scripts that may need broader filesystem/env access).
    use_sandbox = trigger in ("scheduled", "webhook", "chain")
    if use_sandbox:
        executor = SandboxedExecutor(
            max_cpu_seconds=30,
            max_memory_mb=256,
            max_output_bytes=512_000,
            allow_network=False,
            max_file_size_mb=10,
        )
    else:
        from execution.runner import ExecutionLayer
        executor = ExecutionLayer()

    attempts = RETRY_ATTEMPTS.get(s.retry_policy or "none", 1)
    result = None
    for attempt in range(1, attempts + 1):
        if use_sandbox:
            sandbox_result = await executor.run(
                code=s.code,
                language=s.language,
                run_id=script_id,
                inject_secrets=user_secrets,
                timeout=60,
            )
            # Convert SandboxResult to the dict format the rest of this function expects
            result = {
                "status": sandbox_result.status,
                "stdout": sandbox_result.stdout,
                "stderr": sandbox_result.stderr,
                "exit_code": sandbox_result.exit_code,
                "duration_ms": sandbox_result.duration_ms,
            }
        else:
            result = await executor.run(code=s.code, language=s.language, script_id=s.id,
                                        secrets=user_secrets, venv_path=None,
                                        env_vars=None)
        if result["status"] == "success":
            break
        logger.info(f"Script {s.id} ({s.name}) attempt {attempt}/{attempts} failed")

    async with AsyncSessionLocal() as db2:
        run = ScriptRun(script_id=s.id, trigger=trigger, status=result["status"],
                         stdout=result["stdout"], stderr=result["stderr"],
                         exit_code=result["exit_code"], duration_ms=result["duration_ms"],
                         finished_at=datetime.now(timezone.utc))
        db2.add(run)
        await db2.commit()
        await db2.refresh(run)
        run_id = run.id

    # Notifications (G9) -- reuses the exact EventBus().publish(topic, type,
    # data) pattern governance/hitl.py already uses for "hitl.pending" /
    # "hitl.resolved", so the same subscribeToEvents() SSE stream on the
    # frontend can surface script outcomes with zero new plumbing.
    try:
        from communications.bus import EventBus
        notify_field = "notify_on_success" if result["status"] == "success" else "notify_on_failure"
        notify_setting = getattr(s, notify_field, "none")
        if notify_setting and notify_setting != "none":
            await EventBus().publish(
                f"user:{s.owner_id}",
                f"script.{result['status']}",
                {"script_id": s.id, "script_name": s.name, "run_id": run_id,
                 "trigger": trigger, "exit_code": result["exit_code"],
                 "attempts": attempts if result["status"] != "success" else attempt},
            )
    except Exception as e:
        logger.warning(f"Notification publish failed for script {s.id}: {e}")

    # Chaining (G8) -- fire any enabled child scripts whose condition matches
    # this run's outcome, sequentially, awaited.
    try:
        async with AsyncSessionLocal() as db3:
            cond = "on_success" if result["status"] == "success" else "on_failure"
            cr = await db3.execute(
                select(ScriptChain).where(
                    ScriptChain.parent_script_id == s.id,
                    ScriptChain.enabled == True,  # noqa: E712
                    ScriptChain.condition == cond,
                )
            )
            chains = cr.scalars().all()
        for chain in chains:
            await run_and_record(chain.child_script_id, trigger="chain", _depth=_depth + 1)
    except Exception as e:
        logger.warning(f"Chain execution failed for script {s.id}: {e}")

    return {"id": run_id, "status": result["status"], "exit_code": result["exit_code"],
            "duration_ms": result["duration_ms"]}
