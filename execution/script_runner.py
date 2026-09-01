"""Centralized script execution + recording with governance parity.

Canonical chain (durable paths):
  ScriptRun → ExecutionJob → AuthoritySnapshot → Execution → EvidenceRecord

HUMAN_TERMINAL is an explicit PathClass.HUMAN_ONLY exception.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy import select

logger = logging.getLogger("devos.script_runner")

RETRY_ATTEMPTS = {"none": 1, "once": 2, "twice": 3}


async def run_and_record(script_id: str, trigger: str = "manual", _depth: int = 0) -> dict:
    """Run a script end-to-end with the same governance guarantees as workers
    for durable triggers: job + authority snapshot + isolation + evidence.
    """
    from core.database import AsyncSessionLocal, Script, ScriptRun, ScriptChain
    from governance.sandbox import SandboxedExecutor
    from governance.secrets_vault import get_user_secrets_dict
    from governance.execution_pipeline import (
        PathClass, begin_execution_job, complete_execution_job, record_path_evidence,
    )
    from governance.execution_authority import require_authority

    if _depth > 10:
        logger.warning("Script chain too deep (>10), stopping at %s", script_id)
        return {"status": "error", "error": "chain depth exceeded"}

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Script).where(Script.id == script_id))
        s = r.scalar_one_or_none()
        if not s:
            return {"status": "error", "error": "script not found"}
        user_secrets = await get_user_secrets_dict(db, s.owner_id)
        owner_id = s.owner_id
        lang = s.language
        script_name = s.name
        script_code = s.code
        script_retry = s.retry_policy
        notify_on_success = getattr(s, "notify_on_success", "none")
        notify_on_failure = getattr(s, "notify_on_failure", "none")
        tenant_id = getattr(s, "tenant_id", None) or f"user:{owner_id}"

    # Path classification
    if trigger in ("HUMAN_TERMINAL", "human_terminal"):
        path_class = PathClass.HUMAN_ONLY
        use_sandbox = False
        authority_reason = "human terminal script path (explicit exception)"
    else:
        path_class = PathClass.DURABLE
        use_sandbox = True
        authority_reason = f"script runner trigger={trigger}"

    if lang == "python":
        cap = "ucip:execution.python"
    elif lang in ("node", "javascript"):
        cap = "ucip:execution.node"
    elif lang in ("bash", "shell", "sh"):
        cap = "ucip:execution.bash"
    else:
        cap = "ucip:execution.python"

    # 1) Durable job before execution (parity with workers)
    job_id = None
    authority_snapshot = None
    if path_class == PathClass.DURABLE:
        job_id = await begin_execution_job(
            owner_id=owner_id,
            tenant_id=tenant_id,
            job_type="script_run",
            payload={
                "script_id": script_id,
                "trigger": trigger,
                "language": lang,
                "name": script_name,
                "capability": cap,
            },
            path_class=PathClass.DURABLE,
        )

    # 2) Authority snapshot (immutable for this job)
    auth = require_authority(
        path_class=path_class,
        actor_id=owner_id,
        tenant_id=tenant_id,
        capability=cap if use_sandbox else None,
        job_id=job_id,
        reason=authority_reason,
        metadata={
            "script_id": script_id,
            "trigger": trigger,
            "language": lang,
            "name": script_name,
        },
    )
    authority_snapshot = auth.to_dict()

    # 3) Isolation / execution
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

    attempts = RETRY_ATTEMPTS.get(script_retry or "none", 1)
    result = None
    attempt = 0
    for attempt in range(1, attempts + 1):
        if use_sandbox:
            sandbox_result = await executor.run(
                code=script_code,
                language=lang,
                run_id=script_id,
                inject_secrets=user_secrets,
                timeout=60,
            )
            result = {
                "status": sandbox_result.status,
                "stdout": sandbox_result.stdout,
                "stderr": sandbox_result.stderr,
                "exit_code": sandbox_result.exit_code,
                "duration_ms": sandbox_result.duration_ms,
            }
        else:
            result = await executor.run(
                code=script_code,
                language=lang,
                script_id=script_id,
                secrets=user_secrets,
                venv_path=None,
                env_vars=None,
                path_class=path_class.value,
                actor_id=owner_id,
                tenant_id=tenant_id,
                authority_reason=authority_reason,
            )
        if result["status"] == "success":
            break
        logger.info(
            "Script %s (%s) attempt %s/%s failed",
            script_id, script_name, attempt, attempts,
        )

    # 4) Complete job
    if job_id:
        await complete_execution_job(
            job_id,
            status="succeeded" if result and result.get("status") == "success" else "failed",
            result={
                "status": result.get("status"),
                "exit_code": result.get("exit_code"),
                "authority_snapshot": authority_snapshot,
            },
            error=None if result and result.get("status") == "success" else (result or {}).get("stderr"),
        )

    # 5) Evidence (durable only)
    evidence_id = None
    if path_class == PathClass.DURABLE:
        try:
            evidence_id = await record_path_evidence(
                tenant_id=tenant_id,
                owner_id=owner_id,
                goal=f"script:{script_name}",
                path="script_runner",
                status=(result or {}).get("status") or "failed",
                body={
                    "script_id": script_id,
                    "trigger": trigger,
                    "exit_code": (result or {}).get("exit_code"),
                    "authority_snapshot": authority_snapshot,
                    "attempts": attempt,
                },
                execution_job_id=job_id,
                path_class=PathClass.DURABLE,
                require_evidence=True,
            )
        except Exception as ev_err:
            logger.warning("script evidence failed (governance degraded): %s", ev_err)

    # 6) ScriptRun row
    async with AsyncSessionLocal() as db2:
        run = ScriptRun(
            script_id=script_id,
            trigger=trigger,
            status=result["status"],
            stdout=result.get("stdout") or "",
            stderr=result.get("stderr") or "",
            exit_code=result.get("exit_code") if result.get("exit_code") is not None else -1,
            duration_ms=result.get("duration_ms") or 0,
            finished_at=datetime.now(timezone.utc),
        )
        db2.add(run)
        await db2.commit()
        await db2.refresh(run)
        run_id = run.id

    # Notifications
    try:
        from communications.bus import EventBus
        notify_setting = notify_on_success if result["status"] == "success" else notify_on_failure
        if notify_setting and notify_setting != "none":
            await EventBus().publish(
                f"user:{owner_id}",
                f"script.{result['status']}",
                {
                    "script_id": script_id,
                    "script_name": script_name,
                    "run_id": run_id,
                    "trigger": trigger,
                    "exit_code": result.get("exit_code"),
                    "execution_job_id": job_id,
                    "evidence_id": evidence_id,
                },
            )
    except Exception as e:
        logger.warning("Notification publish failed for script %s: %s", script_id, e)

    # Chaining
    try:
        async with AsyncSessionLocal() as db3:
            cond = "on_success" if result["status"] == "success" else "on_failure"
            cr = await db3.execute(
                select(ScriptChain).where(
                    ScriptChain.parent_script_id == script_id,
                    ScriptChain.enabled == True,  # noqa: E712
                    ScriptChain.condition == cond,
                )
            )
            chains = list(cr.scalars().all())
        for chain in chains:
            await run_and_record(chain.child_script_id, trigger="chain", _depth=_depth + 1)
    except Exception as e:
        logger.warning("Chain execution failed for script %s: %s", script_id, e)

    return {
        "id": run_id,
        "status": result["status"],
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "execution_job_id": job_id,
        "evidence_id": evidence_id,
        "authority_snapshot": authority_snapshot,
        "path_class": path_class.value,
    }
