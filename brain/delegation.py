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
    plan_id: Optional[str] = None
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
            "plan_id": self.plan_id,
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


async def _ensure_persona_agent(persona_key: str, user_id: str = "") -> dict:
    """Durable identity + empty profile/soul for this user."""
    try:
        from brain.agent_identity import ensure_agent_identity
        if user_id:
            ident = await ensure_agent_identity(persona_key=persona_key, user_id=user_id)
            return {
                "id": ident["agent_id"],
                "slug": f"persona-{persona_key}",
                "name": persona_key,
                "contract_id": ident.get("agent_id"),
                "capabilities": ident.get("capabilities") or [],
            }
    except Exception as e:
        logger.debug("identity ensure fallback: %s", e)
    try:
        from core.repositories.agency import ensure_agent
        row = await ensure_agent(slug=f"persona-{persona_key}", name=f"{persona_key} agent", kind="persona")
        return dict(row)
    except Exception:
        return {"id": f"agent:{persona_key}", "slug": f"persona-{persona_key}", "name": persona_key}


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
    """Execute specialist via AgentRuntime bound to Persona executable contract."""
    from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
    from brain.personas import get_persona
    from brain.executable_agents import build_contract_for_persona

    persona = get_persona(persona_id)
    persona_prompt = ""
    agent_id = f"agent:{persona_id}"
    runtime_tools: list = []
    if persona is not None:
        contract = build_contract_for_persona(persona)
        agent_id = contract.agent_id
        runtime_tools = list(contract.runtime_tools or [])
        persona_prompt = (persona.system_prompt or "").strip()
        if effective_caps is None:
            caps = list(contract.capabilities) if contract.capabilities else ["fs.read", "fs.write"]
        else:
            caps = list(effective_caps)
    else:
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
        persona_system_prompt=persona_prompt,
        runtime_tools=runtime_tools,
        agent_id=agent_id,
    )
    result = await run_node_on_agent_runtime(req)
    out = result.to_dict()
    out["persona_id"] = persona_id
    out["agent_id"] = agent_id
    return out


async def _emit_progress(on_progress, payload: dict) -> None:
    """Best-effort progress hook for SSE; never fails the mission."""
    if not on_progress:
        return
    try:
        maybe = on_progress(payload)
        if hasattr(maybe, "__await__"):
            await maybe
    except Exception as e:
        logger.debug("on_progress ignored: %s", e)


async def run_delegated_mission(
    *,
    user_id: str,
    goal: str,
    workspace_id: str = "default",
    persona_key: Optional[str] = None,
    constraints: Optional[list] = None,
    max_rounds: Optional[int] = None,
    plan_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    on_progress=None,
) -> DelegationResult:
    """
    Authoritative specialist execution spine:
      Nuha mission → (optional ExecutionPlan id) → A2A delegate → AgentRuntime
      → UCIP tools → Ponytail gate → evidence → accept | correct-delegate

    ``execute_plan`` is not called here; plan_id is correlation only unless
    a future substrate invokes the DAG engine under this mission.
    """
    max_rounds = max_rounds if max_rounds is not None else MAX_CORRECTION_ROUNDS
    persona_key = persona_key or select_persona_for_goal(goal)
    _plan_id = plan_id
    agent = await _ensure_persona_agent(persona_key, user_id=user_id)
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

        from brain.mission_durability import (
            scoped_idempotency_key,
            emit_mission_event,
            begin_mission_saga,
            a2a_message_idempotency_id,
        )

        ikey = None
        if idempotency_key:
            ikey = scoped_idempotency_key(user_id=user_id, client_key=idempotency_key)
        mission = await create_mission(
            user_id=user_id,
            goal=goal,
            workspace_id=workspace_id,
            actor_type="nuha",
            actor_id="nuha",
            plan_id=plan_id,
            idempotency_key=ikey,
            meta={"plan_id": plan_id} if plan_id else {},
        )
        mission_id = mission["id"]
        if mission.get("reused"):
            logger.info("mission reused via idempotency key mission_id=%s", mission_id)
        begin_mission_saga(mission_id=mission_id, plan_id=plan_id)
        emit_mission_event(
            "mission.created",
            mission_id=mission_id,
            user_id=user_id,
            plan_id=plan_id,
            payload={"goal": goal[:200], "reused": bool(mission.get("reused"))},
        )
        task = await add_mission_task(
            mission_id=mission_id,
            description=goal,
            persona_key=persona_key,
            agent_id=agent_id,
            sequence_no=1,
        )
        task_id = task["id"]
        await _emit_progress(on_progress, {
            "status": "agent_progress",
            "phase": "mission_task_assigned",
            "mission_id": mission_id,
            "task_id": task_id,
            "plan_id": plan_id,
            "persona_key": persona_key,
        })
        await record_delegation(
            mission_id=mission_id,
            task_id=task_id,
            from_actor_type="nuha",
            from_actor_id="nuha",
            to_actor_type="agent",
            to_actor_id=agent_id,
            reason="initial_delegation",
        )
        try:
            from brain.agent_identity import (
                Provenance,
                record_task_lifecycle,
                EVENT_TASK_ASSIGNED,
            )
            prov = Provenance.human_via_nuha(user_id, agent_id)
            await record_task_lifecycle(
                event_type=EVENT_TASK_ASSIGNED,
                agent_id=agent_id,
                user_id=user_id,
                provenance=prov,
                mission_id=mission_id,
                task_id=task_id,
                outcome="assigned",
                summary="task assigned via Nuha",
            )
        except Exception as ie:
            logger.debug("task_assigned event: %s", ie)
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
        try:
            from brain.mission_durability import a2a_message_idempotency_id, emit_mission_event
            env.message_id = a2a_message_idempotency_id(
                mission_id=mission_id or "",
                task_id=task_id or "",
                message_type=msg_type,
                round_i=round_i,
            )
        except Exception:
            pass
        await persist_message(env)
        await _emit_progress(on_progress, {
            "status": "agent_progress",
            "phase": "a2a_delegate_sent",
            "mission_id": mission_id,
            "task_id": task_id,
            "plan_id": plan_id,
            "a2a_message_id": env.message_id,
            "round": round_i,
        })
        msg_ids.append(env.message_id)
        parent_msg = env.message_id
        await update_message_status(env.message_id, "delivered")
        try:
            emit_mission_event(
                "mission.delegated",
                mission_id=mission_id or "",
                user_id=user_id,
                task_id=task_id,
                plan_id=_plan_id,
                payload={"message_id": env.message_id, "round": round_i},
            )
        except Exception:
            pass

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
        provider_failure = exec_result.get("provider_failure")
        if provider_failure:
            agent_ok = False
            agent_status = "failed"
            last_error = str(
                exec_result.get("error")
                or provider_failure.get("category")
                or "provider_exhausted"
            )
            await _emit_progress(on_progress, {
                "status": "failed",
                "phase": "provider_failure",
                "mission_id": mission_id,
                "task_id": task_id,
                "persona_key": persona_key,
                "provider": provider_failure.get("provider"),
                "model": provider_failure.get("model"),
                "error": last_error[:200],
                "retry_count": round_i,
                "check_status": "failed",
            })
            # Durable coding snapshot for browser reconnect
            try:
                await _persist_mission_coding_snapshot(
                    mission_id=mission_id,
                    user_id=user_id,
                    payload={
                        "status": "failed",
                        "provider_failure": provider_failure,
                        "error": last_error[:300],
                        "round": round_i,
                    },
                )
            except Exception:
                pass

        # Attribute work to agent
        try:
            from core.repositories.agency import record_work_history

            tools = list(exec_result.get("tools_used") or []) or ["agent_runtime"]
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
                tools_used=tools,
                files_changed=files,
                outcome="success" if agent_ok else "failure",
                summary=(exec_result.get("summary") or "")[:500],
            )
            # Persist tool executions for provenance
            try:
                from brain.agent_identity import (
                    Provenance, record_task_lifecycle, EVENT_TOOL_EXECUTION, EVENT_FILE_CREATED,
                )
                prov = Provenance.human_via_nuha(user_id, agent_id)
                for tool in tools:
                    await record_task_lifecycle(
                        event_type=EVENT_TOOL_EXECUTION,
                        agent_id=agent_id,
                        user_id=user_id,
                        provenance=prov,
                        mission_id=mission_id,
                        task_id=task_id,
                        tools_used=[tool],
                        files_changed=files,
                        outcome="success" if agent_ok else "failure",
                        summary=f"tool:{tool}",
                    )
                if agent_ok and files:
                    await record_task_lifecycle(
                        event_type=EVENT_FILE_CREATED,
                        agent_id=agent_id,
                        user_id=user_id,
                        provenance=prov,
                        mission_id=mission_id,
                        task_id=task_id,
                        tools_used=tools,
                        files_changed=files,
                        outcome="success",
                        summary="files written via AgentRuntime tools",
                    )
            except Exception as te:
                logger.debug("tool events: %s", te)
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

        await _emit_progress(on_progress, {
            "status": "worker_completed" if agent_ok else "agent_progress",
            "phase": "specialist_execution",
            "mission_id": mission_id,
            "task_id": task_id,
            "plan_id": plan_id,
            "ok": bool(agent_ok),
            "files_count": len(files) if files else 0,
            "round": round_i,
        })
        if not agent_ok:
            last_error = exec_result.get("error") or agent_status
            await update_message_status(env.message_id, "failed", error=last_error)
            continue

        # 4. Agent → Ponytail
        await _emit_progress(on_progress, {
            "status": "validation_started",
            "phase": "ponytail",
            "mission_id": mission_id,
            "task_id": task_id,
            "plan_id": plan_id,
            "round": round_i,
        })
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

        from cognitive.ponytail_gate import validate_agent_artifacts, assert_accepted

        gate = await validate_agent_artifacts(
            user_id=user_id,
            project_id=workspace_id,
            goal=goal,
            files_changed=files,
            agent_id=agent_id,
            mission_id=mission_id,
            task_id=task_id,
            requirements=goal,
            force_code_gate=(persona_key in ("web", "code", "data", "automation", "script_writer")),
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
            if not getattr(gate, "evidence_id", None):
                last_error = "ponytail_passed_without_durable_evidence"
                logger.warning(last_error)
                continue
            try:
                assert_accepted(gate)
            except PermissionError as pe:
                last_error = str(pe)
                continue
            try:
                from brain.agent_identity import (
                    Provenance,
                    record_task_lifecycle,
                    append_validated_lesson,
                    EVENT_PONYTAIL_VALIDATION,
                    EVENT_TASK_COMPLETED,
                )
                prov = Provenance.human_via_nuha(user_id, agent_id)
                await record_task_lifecycle(
                    event_type=EVENT_PONYTAIL_VALIDATION,
                    agent_id=agent_id,
                    user_id=user_id,
                    provenance=prov,
                    mission_id=mission_id,
                    task_id=task_id,
                    files_changed=files,
                    outcome="passed",
                    summary=gate.summary,
                    ponytail_check_id=gate.evidence_id,
                )
                await record_task_lifecycle(
                    event_type=EVENT_TASK_COMPLETED,
                    agent_id=agent_id,
                    user_id=user_id,
                    provenance=prov,
                    mission_id=mission_id,
                    task_id=task_id,
                    files_changed=files,
                    outcome="success",
                    summary="accepted after Ponytail",
                    ponytail_check_id=gate.evidence_id,
                )
                # Explicit validated lesson only (bounded operational fact)
                await append_validated_lesson(
                    agent_id=agent_id,
                    user_id=user_id,
                    lesson=f"Delivered {len(files)} artifact(s) under Ponytail for: {(goal or '')[:80]}",
                    source_event=EVENT_PONYTAIL_VALIDATION,
                    mission_id=mission_id,
                )
                # Website goals: authoritative validate_website_artifacts + artifact metadata
                try:
                    from brain.artifact_scaffold import _is_website_goal
                    from brain.orchestration_verify import validate_website_artifacts
                    from core.repositories.agency import upsert_artifact_metadata
                    if _is_website_goal(goal) or persona_key == "web":
                        wv = await validate_website_artifacts(
                            user_id=user_id,
                            workspace_id=workspace_id,
                            goal=goal,
                        )
                        if not wv.get("valid"):
                            last_error = "website_validation:" + ",".join(wv.get("errors") or ["invalid"])
                            # treat as ponytail-class failure for correction loop
                            gate.passed = False
                            raise RuntimeError(last_error)
                        for path in (wv.get("files_checked") or files or []):
                            pp = path.get("path") if isinstance(path, dict) else str(path)
                            if not pp:
                                continue
                            await upsert_artifact_metadata(
                                user_id=user_id,
                                project_id=workspace_id,
                                path=pp,
                                agent_id=agent_id,
                                mission_id=mission_id,
                                task_id=task_id,
                                actor_type="agent",
                                actor_id=agent_id,
                            )
                        # Prefer validated entry point in files list
                        if wv.get("entry_point"):
                            files = list(files or [])
                            if not any(
                                (f.get("path") if isinstance(f, dict) else f) == wv["entry_point"]
                                for f in files
                            ):
                                files.append({"path": wv["entry_point"], "kind": "validated"})
                except RuntimeError:
                    raise
                except Exception as ve:
                    logger.debug("website validate/artifact meta: %s", ve)
            except RuntimeError as rexc:
                last_error = str(rexc)
                logger.info("post-ponytail website validation failed: %s", last_error)
                continue
            except Exception as ie:
                logger.debug("completion identity: %s", ie)
            # Accepted only after authoritative mission acceptance (not model/provider alone)
            from brain.mission_acceptance import evaluate_mission_acceptance

            # Build coding evidence from real execution (never fabricated)
            coding_ev = None
            try:
                from brain.coding_evidence import build_coding_evidence, persist_coding_evidence
                coding_ev = build_coding_evidence(
                    mission_id=mission_id or "",
                    project_id=workspace_id or "default",
                    user_id=user_id,
                    agent_id=agent_id,
                    persona_id=persona_key,
                    files_changed=files,
                    commands=list(exec_result.get("commands") or []),
                    validation={"ok": True, "ponytail": True, "works": True},
                    artifacts=[f.get("path") if isinstance(f, dict) else f for f in files],
                    success=True,
                )
                eid = persist_coding_evidence(coding_ev)
                if eid and eid not in evidence_refs:
                    evidence_refs.append(eid)
            except Exception as ce:
                logger.debug("coding evidence build: %s", ce)

            acceptance = evaluate_mission_acceptance(
                execution_ok=True,
                status="accepted",
                files_changed=files,
                ponytail=gate.to_dict() if hasattr(gate, "to_dict") else {
                    "passed": bool(getattr(gate, "passed", False)),
                    "evidence_id": getattr(gate, "evidence_id", None),
                    "applicable": getattr(gate, "applicable", True),
                },
                evidence_refs=evidence_refs,
                user_id=user_id,
                mission_id=mission_id,
                coding_evidence=coding_ev.to_dict() if coding_ev else None,
                require_coding_evidence=bool(files),
            )
            try:
                await _persist_mission_coding_snapshot(
                    mission_id=mission_id,
                    user_id=user_id,
                    payload={
                        "status": "accepted" if acceptance.get("ok") else "failed",
                        "acceptance": acceptance,
                        "files_changed": [
                            f.get("path") if isinstance(f, dict) else f for f in files
                        ],
                        "evidence_id": (coding_ev.evidence_id if coding_ev else None),
                    },
                )
            except Exception:
                pass
            if not acceptance.get("ok"):
                last_error = acceptance.get("reason") or "mission_acceptance_failed"
                logger.info("mission acceptance denied after Ponytail: %s", last_error)
                continue
            return DelegationResult(
                plan_id=_plan_id,
                ok=True,
                mission_id=mission_id,
                task_id=task_id,
                agent_id=agent_id,
                persona_key=persona_key,
                status="accepted",
                files_changed=files,
                evidence_refs=evidence_refs,
                a2a_message_ids=msg_ids,
                ponytail=gate.to_dict() if hasattr(gate, "to_dict") else {"passed": True},
                rounds=round_i,
            )

        last_error = gate.summary or "ponytail_failed"
        try:
            from brain.agent_identity import (
                Provenance,
                record_task_lifecycle,
                EVENT_PONYTAIL_VALIDATION,
                EVENT_CORRECTION_REQUESTED,
            )
            prov = Provenance.human_via_nuha(user_id, agent_id)
            await record_task_lifecycle(
                event_type=EVENT_PONYTAIL_VALIDATION,
                agent_id=agent_id,
                user_id=user_id,
                provenance=prov,
                mission_id=mission_id,
                task_id=task_id,
                files_changed=files,
                outcome="failed",
                summary=last_error,
            )
            await record_task_lifecycle(
                event_type=EVENT_CORRECTION_REQUESTED,
                agent_id=agent_id,
                user_id=user_id,
                provenance=prov,
                mission_id=mission_id,
                task_id=task_id,
                outcome="pending",
                summary=last_error,
            )
        except Exception as ie:
            logger.debug("correction identity: %s", ie)
        # Correction will loop — Nuha delegates again (not silent patch)

    return DelegationResult(
            plan_id=_plan_id,
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
