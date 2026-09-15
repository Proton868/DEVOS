"""
Nuha → Agent delegation layer.

CORE RULE: Nuha orchestrates. Agents execute. Ponytail validates. DB records truth.

Uses existing Mission / UCIP / AgentRuntime — does not create a second runtime.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from brain.a2a import A2AEnvelope, list_messages, persist_message, update_message_status

logger = logging.getLogger("devos.delegation")

MAX_CORRECTION_ROUNDS = int(os.environ.get("DEVOS_DELEGATION_MAX_ROUNDS", "3"))


@dataclass
class DelegationResult:
    ok: bool
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    persona_key: Optional[str] = None
    status: str = "unknown"
    files_changed: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)
    a2a_message_ids: list = field(default_factory=list)
    ponytail: Optional[dict] = None
    error: Optional[str] = None
    execution_path: str = "A2A_DELEGATION"
    rounds: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "persona_key": self.persona_key,
            "status": self.status,
            "files_changed": self.files_changed,
            "evidence_refs": self.evidence_refs,
            "a2a_message_ids": self.a2a_message_ids,
            "ponytail": self.ponytail,
            "error": self.error,
            "execution_path": self.execution_path,
            "rounds": self.rounds,
        }


def select_persona_for_goal(goal: str) -> str:
    """Map goal → persona_key. Nuha selection policy (not execution)."""
    g = (goal or "").lower()
    try:
        from brain.artifact_scaffold import _is_website_goal

        if _is_website_goal(goal):
            return "web"
    except Exception:
        if any(k in g for k in ("website", "landing page", "html", "webpage")):
            return "web"
    if any(k in g for k in ("design", "ui", "mockup", "figma")):
        return "design"
    if any(k in g for k in ("research", "investigate", "search")):
        return "research"
    if any(k in g for k in ("workflow", "automat", "n8n", "zapier")):
        return "automation"
    if any(k in g for k in ("data", "sql", "pipeline", "etl")):
        return "data"
    return "code"


async def _ensure_persona_agent(persona_key: str) -> dict:
    try:
        from brain.executable_agents import build_registry
        reg = build_registry()
        contract = reg.get(persona_key)
        agent_id = contract.agent_id if contract else f"agent:{persona_key}"
    except Exception:
        agent_id = f"agent:{persona_key}"
    try:
        from core.repositories.agency import ensure_agent

        row = await ensure_agent(
            slug=f"persona-{persona_key}",
            name=f"{persona_key} agent",
            kind="persona",
        )
        # Prefer stable contract id when DB id differs
        row = dict(row)
        row["contract_id"] = agent_id
        return row
    except Exception:
        return {"id": agent_id, "slug": f"persona-{persona_key}", "name": persona_key, "contract_id": agent_id}


async def _run_agent_node(
    *,
    user_id: str,
    workspace_id: str,
    plan_id: str,
    node_id: str,
    persona_id: str,
    objective: str,
    effective_caps: Optional[list] = None,
) -> dict:
    from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime

    caps = effective_caps or ["fs.read", "fs.write", "shell.exec"]
    req = NodeExecutionRequest(
        plan_id=plan_id,
        node_id=node_id,
        user_id=user_id,
        workspace_id=workspace_id,
        persona_id=persona_id,
        objective=objective,
        effective_caps=caps,
        authorization_decision="allow",
    )
    result = await run_node_on_agent_runtime(req)
    return result.to_dict()


async def run_delegated_mission(
    *,
    user_id: str,
    goal: str,
    workspace_id: str = "default",
    persona_key: Optional[str] = None,
    constraints: Optional[list] = None,
    max_rounds: Optional[int] = None,
) -> DelegationResult:
    """
    Full loop:
      Nuha mission → task → A2A delegate → AgentRuntime → evidence
      → Ponytail gate → accept | correct-delegate
    """
    max_rounds = max_rounds if max_rounds is not None else MAX_CORRECTION_ROUNDS
    persona_key = persona_key or select_persona_for_goal(goal)
    agent = await _ensure_persona_agent(persona_key)
    agent_id = agent["id"]
    msg_ids: list[str] = []
    evidence_refs: list[str] = []

    # 1. Mission
    mission_id = None
    task_id = None
    try:
        from core.repositories.agency import (
            create_mission,
            add_mission_task,
            record_delegation,
            record_work_history,
        )

        mission = await create_mission(
            user_id=user_id,
            goal=goal,
            workspace_id=workspace_id,
            actor_type="nuha",
            actor_id="nuha",
        )
        mission_id = mission["id"]
        task = await add_mission_task(
            mission_id=mission_id,
            description=goal,
            persona_key=persona_key,
            agent_id=agent_id,
            sequence_no=1,
        )
        task_id = task["id"]
        await record_delegation(
            mission_id=mission_id,
            task_id=task_id,
            from_actor_type="nuha",
            from_actor_id="nuha",
            to_actor_type="agent",
            to_actor_id=agent_id,
            reason="initial_delegation",
        )
    except Exception as e:
        logger.warning("mission persist degraded: %s", e)
        import uuid

        mission_id = mission_id or str(uuid.uuid4())
        task_id = task_id or str(uuid.uuid4())

    parent_msg: Optional[str] = None
    last_files: list = []
    last_error: Optional[str] = None

    for round_i in range(1, max_rounds + 1):
        # 2. A2A delegate (or correct)
        msg_type = "delegate" if round_i == 1 else "correct"
        objective = goal if round_i == 1 else (
            f"CORRECTION round {round_i}: previous delivery failed Ponytail validation. "
            f"Fix and re-deliver. Goal: {goal}. Error: {last_error or 'validation_failed'}"
        )
        env = A2AEnvelope.create(
            mission_id=mission_id,
            task_id=task_id,
            sender_type="nuha",
            sender_id="nuha",
            recipient_agent_id=agent_id,
            message_type=msg_type,
            objective=objective,
            constraints=constraints or [],
            requested_capabilities=["fs.write", "fs.read"],
            parent_message_id=parent_msg,
            payload={"persona_key": persona_key, "round": round_i},
            status="queued",
        )
        await persist_message(env)
        msg_ids.append(env.message_id)
        parent_msg = env.message_id
        await update_message_status(env.message_id, "delivered")

        # 3. Agent executes via AgentRuntime (not Nuha)
        await update_message_status(env.message_id, "processing")
        exec_result = await _run_agent_node(
            user_id=user_id,
            workspace_id=workspace_id,
            plan_id=mission_id,
            node_id=task_id,
            persona_id=persona_key,
            objective=objective,
        )
        files = list(exec_result.get("files_changed") or [])
        last_files = files
        agent_ok = bool(exec_result.get("success"))
        agent_status = exec_result.get("status") or ("succeeded" if agent_ok else "failed")

        # Attribute work to agent
        try:
            from core.repositories.agency import record_work_history

            await record_work_history(
                agent_id=agent_id,
                user_id=user_id,
                actor_type="agent",
                actor_id=agent_id,
                delegated_by_type="nuha",
                delegated_by_id="nuha",
                mission_id=mission_id,
                task_id=task_id,
                action="agent_runtime_execution",
                tools_used=["agent_runtime"],
                files_changed=files,
                outcome="success" if agent_ok else "failure",
                summary=(exec_result.get("summary") or "")[:500],
            )
        except Exception as e:
            logger.debug("work_history: %s", e)

        # Agent → Nuha completion/failure message
        reply = A2AEnvelope.create(
            mission_id=mission_id,
            task_id=task_id,
            sender_type="agent",
            sender_id=agent_id,
            recipient_agent_id="nuha",
            message_type="complete" if agent_ok else "fail",
            objective=objective,
            artifact_refs=[
                (f.get("path") if isinstance(f, dict) else str(f)) for f in files
            ],
            parent_message_id=env.message_id,
            payload={"execution": exec_result},
            status="completed" if agent_ok else "failed",
        )
        await persist_message(reply)
        msg_ids.append(reply.message_id)

        if not agent_ok:
            last_error = exec_result.get("error") or agent_status
            await update_message_status(env.message_id, "failed", error=last_error)
            continue

        # 4. Agent → Ponytail
        pt_req = A2AEnvelope.create(
            mission_id=mission_id,
            task_id=task_id,
            sender_type="agent",
            sender_id=agent_id,
            recipient_agent_id="ponytail",
            message_type="ponytail_request",
            objective=goal,
            artifact_refs=[
                (f.get("path") if isinstance(f, dict) else str(f)) for f in files
            ],
            parent_message_id=reply.message_id,
            status="queued",
        )
        await persist_message(pt_req)
        msg_ids.append(pt_req.message_id)

        from cognitive.ponytail_gate import validate_agent_artifacts

        gate = await validate_agent_artifacts(
            user_id=user_id,
            project_id=workspace_id,
            goal=goal,
            files_changed=files,
            agent_id=agent_id,
            mission_id=mission_id,
            task_id=task_id,
        )
        if gate.evidence_id:
            evidence_refs.append(gate.evidence_id)

        # Ponytail → Nuha
        pt_res = A2AEnvelope.create(
            mission_id=mission_id,
            task_id=task_id,
            sender_type="ponytail",
            sender_id="ponytail",
            recipient_agent_id="nuha",
            message_type="ponytail_result",
            objective=goal,
            artifact_refs=pt_req.artifact_refs,
            evidence_refs=[gate.evidence_id] if gate.evidence_id else [],
            parent_message_id=pt_req.message_id,
            payload=gate.to_dict(),
            status="completed" if gate.passed else "failed",
        )
        await persist_message(pt_res)
        msg_ids.append(pt_res.message_id)

        if gate.passed:
            # Accepted — only now Nuha treats as success
            return DelegationResult(
                ok=True,
                mission_id=mission_id,
                task_id=task_id,
                agent_id=agent_id,
                persona_key=persona_key,
                status="accepted",
                files_changed=files,
                evidence_refs=evidence_refs,
                a2a_message_ids=msg_ids,
                ponytail=gate.to_dict(),
                rounds=round_i,
            )

        last_error = gate.summary or "ponytail_failed"
        # Correction will loop — Nuha delegates again (not silent patch)

    return DelegationResult(
        ok=False,
        mission_id=mission_id,
        task_id=task_id,
        agent_id=agent_id,
        persona_key=persona_key,
        status="failed",
        files_changed=last_files,
        evidence_refs=evidence_refs,
        a2a_message_ids=msg_ids,
        ponytail={"passed": False, "summary": last_error},
        error=last_error or "policy_limit_exceeded",
        rounds=max_rounds,
    )
