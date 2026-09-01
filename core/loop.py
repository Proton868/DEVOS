"""
DevOS — Production Brain↔Execution Loop (v2 with full governance)
All 10 audit gaps addressed. Every action flows through:
  ToolValidator → UCIPGateway → SandboxedExecutor → ObservabilityStore → CheckpointManager
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("devos.loop")


class StepType(str, Enum):
    THINK   = "think"
    PLAN    = "plan"
    WRITE   = "write"
    EXECUTE = "execute"
    OBSERVE = "observe"
    DECIDE  = "decide"
    REFLECT = "reflect"
    HITL    = "hitl"
    DENIED  = "denied"
    DONE    = "done"


class LoopDecision(str, Enum):
    CONTINUE  = "continue"
    RETRY     = "retry"
    ESCALATE  = "escalate"
    COMPLETE  = "complete"
    ABORT     = "abort"


@dataclass
class LoopStep:
    type:      StepType
    content:   str
    metadata:  dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LoopState:
    id:                  str = field(default_factory=lambda: str(uuid.uuid4()))
    goal:                str = ""
    steps:               list = field(default_factory=list)
    scripts_written:     list = field(default_factory=list)
    execution_results:   list = field(default_factory=list)
    final_answer:        str = ""
    decision:            LoopDecision = LoopDecision.CONTINUE
    iteration:           int = 0
    total_tokens:        int = 0
    total_latency_ms:    int = 0
    ucip_denials:        int = 0
    hitl_requests:       int = 0
    sandbox_violations:  list = field(default_factory=list)
    created_at:          datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expected_outcome:    Optional[object] = None   # governance.identity.ExpectedOutcome, if declared
    outcome_correction_attempts: int = 0
    outcome_valid:       Optional[bool] = None      # None = no schema was declared, so no check ran
    subtasks:            list = field(default_factory=list)   # cognitive.decomposer.Subtask, if decompose_first was used
    reflections:         list = field(default_factory=list)   # cognitive.reflector.Reflection history, if use_reflection was used
    reflection_correction_attempts: int = 0
    use_reflection:      bool = False

    def add_step(self, type, content, **meta):
        step = LoopStep(type=type, content=content, metadata=meta)
        self.steps.append(step)
        return step

    @property
    def succeeded(self):
        return self.decision == LoopDecision.COMPLETE

    def to_dict(self):
        return {
            "id": self.id, "goal": self.goal,
            "final_answer": self.final_answer, "decision": self.decision,
            "iterations": self.iteration, "scripts_written": len(self.scripts_written),
            "executions": len(self.execution_results),
            "total_tokens": self.total_tokens, "total_latency_ms": self.total_latency_ms,
            "ucip_denials": self.ucip_denials, "hitl_requests": self.hitl_requests,
            "sandbox_violations": self.sandbox_violations,
            "expected_outcome": self.expected_outcome.to_dict() if self.expected_outcome else None,
            "outcome_valid": self.outcome_valid,
            "subtasks": [t.to_dict() if hasattr(t, "to_dict") else t for t in self.subtasks],
            "reflections": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.reflections],
            "steps": [{"type": s.type, "content": s.content[:300], "meta": s.metadata}
                      for s in self.steps],
        }


BRAIN_SYSTEM_PROMPT = """You are the Brain of DevOS — an autonomous AI agent under UCIP governance.

TOOLS (respond with exactly one per turn as JSON):
  write_python(code)  — execute Python in sandbox (no network)
  write_bash(code)    — execute Bash in sandbox
  search_web(query)   — web search via Tavily
  recall_memory(query) — retrieve past context
  mark_complete(answer) — declare goal done with final answer
  ask_user(question)  — escalate to user when stuck
  spawn_agent(worker, goal) — delegate a subtask to a specialized Worker persona
  graph_remember(from_type, from_name, to_type, to_name, relation_type) — record a fact in the knowledge graph
  graph_query(name)    — look up what's connected to a name in the knowledge graph

RULES:
  1. Think first (one sentence), then pick one tool
  2. Write complete, runnable code — no placeholders
  3. If code fails, read the error and write a fixed version
  4. Never repeat the exact same failing code
  5. Use mark_complete as soon as the goal is achieved

Respond ONLY in this JSON format (no markdown):
{"thought":"...","action":"tool_name","action_input":"...","description":"one line"}"""


class BrainExecutionLoop:
    """Production Brain-Execution loop with all governance layers."""

    def __init__(self, user_id, session_id, provider=None, model=None,
                 trust_level_str="OPERATOR", on_step=None, budget_override=None,
                 agent_identity=None, persona_prompt=None):
        self.user_id    = user_id
        self.session_id = session_id
        self.provider   = provider
        self.model      = model
        self.on_step    = on_step
        self.persona_prompt = persona_prompt

        from governance.ucip import AgentIdentity, BudgetPolicy, TrustLevel, UCIPGateway
        if agent_identity is not None:
            # A pre-built (typically delegated, see AgentIdentity.delegate())
            # identity was supplied — use it as-is rather than minting a
            # fresh root-ish one. This is how Workers run under a properly
            # narrowed identity instead of the requesting user's full trust
            # level. See workers/runtime.py for the actual caller.
            self.agent = agent_identity
        else:
            trust = TrustLevel.from_str(trust_level_str)
            self.agent = AgentIdentity.create(user_id, session_id, trust)
        self.gateway = UCIPGateway(self.agent, BudgetPolicy(**(budget_override or {})))

        from governance.sandbox import SandboxedExecutor
        self.sandbox = SandboxedExecutor(max_cpu_seconds=30, max_memory_mb=256)

        from governance.observability import ObservabilityStore
        self.obs = ObservabilityStore()

        from governance.checkpoint import CheckpointManager
        self.ckpt = CheckpointManager()

        from governance.hitl import HITLQueue
        self.hitl = HITLQueue()

        from governance.tool_contracts import ToolValidator
        self.validator = ToolValidator()

        from memory.store import MemoryStore
        self.memory = MemoryStore()

    async def run(self, goal, expected_outcome=None, decompose_first=False, use_reflection=False):
        from governance.ratelimit import RateLimiter
        from brain.llm import BrainLLM

        rl = RateLimiter()
        allowed, reason = await rl.check_loop(self.user_id)
        if not allowed:
            s = LoopState(goal=goal)
            s.final_answer = f"Rate limited: {reason}"
            s.decision = LoopDecision.ABORT
            return s

        state = LoopState(goal=goal, expected_outcome=expected_outcome)
        # One BrainLLM instance for this whole run, not a separate one for
        # planning and another for the main loop — found via a coordinator
        # concurrency test (record.md Session 9) that surfaced ~30-60ms of
        # pure waste on every run() call, most of it from a BrainLLM
        # instance that decompose_first=False (the common case) never
        # even used.
        brain = BrainLLM(self.provider, self.model, user_id=self.user_id)

        if decompose_first:
            from cognitive.decomposer import GoalDecomposer
            subtasks = await GoalDecomposer().decompose(goal, brain)
            state.subtasks = subtasks
            plan_summary = "\n".join(f"- [{t.id}] {t.description}"
                                      + (f" (after: {', '.join(t.depends_on)})" if t.depends_on else "")
                                      for t in subtasks)
            state.add_step(StepType.PLAN, f"Decomposed into {len(subtasks)} subtask(s):\n{plan_summary}")
            await self._emit(state.steps[-1])

        state.use_reflection = use_reflection  # read by _loop's mark_complete handling
        rl.register_loop(state.id)
        self.obs.start_trace(state.id, self.agent.agent_id, self.session_id, goal,
                             self.provider or "ollama", self.model or "default")
        try:
            await self._loop(state, brain)
        except Exception as e:
            logger.error(f"Loop crashed: {e}", exc_info=True)
            state.final_answer = f"Internal error: {e}"
            state.decision = LoopDecision.ABORT
        finally:
            rl.unregister_loop(state.id)
            self.ckpt.delete(state.id)
            self.obs.finish_trace(
                state.id,
                status="complete" if state.succeeded else str(state.decision),
                decision=str(state.decision), iterations=state.iteration,
                total_tokens=state.total_tokens,
                total_latency_ms=state.total_latency_ms,
                tool_calls=len(state.execution_results),
                final_answer=state.final_answer,
            )
        return state

    async def _loop(self, state, brain):
        hits = await self.memory.recall(self.user_id, state.goal, limit=3)
        # If persona_prompt is set, it's expected to be a FULLY composed
        # system prompt already (see brain.agents.build_agent_system_prompt,
        # which workers/runtime.py uses) — not something to concatenate
        # again here. Composition responsibility lives with whoever builds
        # the persona prompt, not duplicated in the loop.
        system_prompt = self.persona_prompt or BRAIN_SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_prompt}]
        if hits:
            ctx = "\n".join(f"- {h.get('content','')}" for h in hits)
            messages.append({"role": "system", "content": f"[Past context]:\n{ctx}"})
        messages.append({"role": "user", "content": f"Goal: {state.goal}"})
        if state.subtasks:
            # Bug found via testing (record.md Session 7): the PLAN step was
            # logged for audit purposes but never actually reached the
            # Brain's context, so decompose_first computed a plan the Brain
            # never saw. Fixed by folding it into the seed messages here.
            plan_text = "\n".join(f"- [{t.id}] {t.description}"
                                   + (f" (after: {', '.join(t.depends_on)})" if t.depends_on else "")
                                   for t in state.subtasks)
            messages.append({"role": "user", "content":
                f"This goal has been broken into subtasks. Work through them in dependency order:\n{plan_text}"})

        while True:
            # Budget check
            budget_err = self.gateway.tick_iteration()
            if budget_err:
                state.add_step(StepType.DECIDE, f"Budget: {budget_err}")
                state.final_answer = f"Stopped: {budget_err}"
                state.decision = LoopDecision.ABORT
                await self._emit(state.steps[-1])
                break

            state.iteration += 1
            t0 = time.perf_counter_ns()
            resp = await brain.decide(messages)
            latency_ms = (time.perf_counter_ns() - t0) // 1_000_000
            state.total_latency_ms += latency_ms

            if not resp:
                state.decision = LoopDecision.ABORT
                state.final_answer = "Brain failed to respond."
                break

            action       = resp.get("action", "mark_complete")
            action_input = resp.get("action_input", "")
            thought      = resp.get("thought", "")
            description  = resp.get("description", action)

            think = state.add_step(StepType.THINK, thought,
                                    action=action, description=description,
                                    iteration=state.iteration)
            await self._emit(think)
            messages.append({"role": "assistant", "content": json.dumps(resp)})

            # Tool contract validation
            valid, val_err = self.validator.validate(action, action_input)
            if not valid:
                step = state.add_step(StepType.DENIED, f"Validation: {val_err}")
                await self._emit(step)
                messages.append({"role": "user",
                                 "content": f"[VALIDATION ERROR] {val_err}\n\nFix and retry."})
                continue

            # UCIP policy gate
            ucip = self.gateway.request(action, action_input,
                                         context={"iteration": state.iteration})
            if ucip.decision == "DENY":
                state.ucip_denials += 1
                step = state.add_step(StepType.DENIED, f"UCIP DENIED: {ucip.reason}")
                await self._emit(step)
                messages.append({"role": "user",
                                 "content": f"[UCIP DENIED] {ucip.reason}\nChoose a permitted action."})
                if state.ucip_denials >= 3:
                    state.decision = LoopDecision.ABORT
                    state.final_answer = "Too many permission violations."
                    break
                continue

            if ucip.decision == "ESCALATE_TO_HUMAN":
                state.hitl_requests += 1
                step = state.add_step(StepType.HITL,
                                       f"Human approval needed: {ucip.reason}",
                                       action=action, cap=ucip.cap_required)
                await self._emit(step)
                hitl_req = await self.hitl.submit(
                    loop_id=state.id, agent_id=self.agent.agent_id,
                    user_id=self.user_id, action=action,
                    action_input=action_input, description=description,
                    cap_required=ucip.cap_required or "", reason=ucip.reason,
                )
                approved = await self.hitl.wait_for_decision(hitl_req.id)
                if not approved:
                    state.add_step(StepType.DENIED, f"HITL denied: {action}")
                    messages.append({"role": "user",
                                     "content": f"[HITL DENIED] Human denied '{action}'. Try an alternative."})
                    continue

            # Worker-to-Worker delegation
            if action == "spawn_agent":
                MAX_DELEGATION_DEPTH = 3
                current_depth = len(self.agent.delegation_chain)
                if current_depth >= MAX_DELEGATION_DEPTH:
                    state.add_step(StepType.DENIED,
                        f"spawn_agent blocked: delegation depth {current_depth} already at the limit "
                        f"({MAX_DELEGATION_DEPTH}) — cannot spawn a further sub-Worker")
                    await self._emit(state.steps[-1])
                    messages.append({"role": "user", "content":
                        "[DEPTH LIMIT] Cannot spawn another Worker — delegation chain is already at "
                        "the maximum depth. Complete this task yourself or with existing tools."})
                    continue
                try:
                    spawn_req = json.loads(action_input)
                    worker_slug, sub_goal = spawn_req["worker"], spawn_req["goal"]
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    state.add_step(StepType.DENIED, f"spawn_agent: malformed input ({e})")
                    await self._emit(state.steps[-1])
                    messages.append({"role": "user", "content":
                        '[FORMAT ERROR] spawn_agent action_input must be JSON: '
                        '{"worker": "<slug>", "goal": "<sub-task description>"}'})
                    continue

                state.add_step(StepType.THINK, f"Delegating to '{worker_slug}': {sub_goal[:80]}")
                await self._emit(state.steps[-1])
                from workers.runtime import WorkerRuntime, UnknownWorkerError
                try:
                    sub_state, sub_identity = await WorkerRuntime().run(
                        worker_slug, sub_goal, self.agent,  # self.agent, not the original requester —
                        provider=self.provider, model=self.model,  # this is what makes delegation CHAIN,
                    )                                              # not just fan out from one root each time
                    sub_success = sub_state.decision == "complete"
                    obs_text = f"Worker '{worker_slug}' {'completed' if sub_success else 'did not complete'}: {sub_state.final_answer[:400]}"
                except UnknownWorkerError as e:
                    obs_text = f"spawn_agent failed: {e}"
                    sub_success = False
                state.add_step(StepType.OBSERVE, obs_text[:400])
                await self._emit(state.steps[-1])
                messages.append({"role": "user", "content": f"[Sub-Worker result]\n{obs_text}\n\nWhat next?"})
                continue

            # Knowledge graph (Memory: Semantic division, Session 15)
            if action == "graph_remember":
                try:
                    fact = json.loads(action_input)
                    from memory.graph import KnowledgeGraph
                    result = KnowledgeGraph().upsert_relationship(
                        self.user_id, fact["from_type"], fact["from_name"],
                        fact["to_type"], fact["to_name"], fact["relation_type"],
                        fact.get("properties"),
                    )
                    obs_text = f"Recorded: {fact['from_name']} --{fact['relation_type']}--> {fact['to_name']}"
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    obs_text = (f"graph_remember: malformed input ({e}). Expected JSON: "
                                '{"from_type":..,"from_name":..,"to_type":..,"to_name":..,"relation_type":..}')
                state.add_step(StepType.OBSERVE, obs_text)
                await self._emit(state.steps[-1])
                messages.append({"role": "user", "content": obs_text})
                continue

            if action == "graph_query":
                try:
                    query = json.loads(action_input) if action_input.strip().startswith("{") else {"name": action_input}
                    from memory.graph import KnowledgeGraph
                    result = KnowledgeGraph().query_by_name(
                        self.user_id, query["name"], query.get("relation_type"),
                        query.get("direction", "both"), query.get("depth", 1),
                    )
                    if not result["found"]:
                        obs_text = f"'{query['name']}' is not in the knowledge graph yet."
                    elif not result["related"]:
                        obs_text = f"'{query['name']}' is in the graph but has no recorded relationships matching this query."
                    else:
                        lines = [f"- {r['via_relationship']['relation_type']} -> {r['entity']['name']}" for r in result["related"]]
                        obs_text = f"'{query['name']}' relationships:\n" + "\n".join(lines)
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    obs_text = f"graph_query: malformed input ({e}). Expected JSON: {{\"name\": \"...\"}} or a plain name string."
                state.add_step(StepType.OBSERVE, obs_text[:500])
                await self._emit(state.steps[-1])
                messages.append({"role": "user", "content": obs_text})
                continue

            # Terminal actions
            if action == "mark_complete":
                if state.expected_outcome and state.expected_outcome.schema:
                    valid, errors = state.expected_outcome.validate(action_input)
                    state.outcome_valid = valid
                    if not valid:
                        state.outcome_correction_attempts += 1
                        if state.outcome_correction_attempts <= state.expected_outcome.max_correction_attempts:
                            state.add_step(StepType.OBSERVE,
                                f"mark_complete rejected — output didn't match expected_outcome_schema: {'; '.join(errors)}")
                            await self._emit(state.steps[-1])
                            messages.append({"role": "user", "content":
                                f"[OUTCOME REJECTED] Your answer must match this schema: {json.dumps(state.expected_outcome.schema)}. "
                                f"Errors: {'; '.join(errors)}. Respond again with mark_complete using corrected JSON."})
                            continue
                        # Exhausted correction attempts — surface to human rather than
                        # silently accepting an answer that doesn't meet the declared bar.
                        state.add_step(StepType.HITL,
                            f"Could not produce output matching expected_outcome_schema after "
                            f"{state.expected_outcome.max_correction_attempts} attempts: {'; '.join(errors)}")
                        await self._emit(state.steps[-1])
                        state.decision = LoopDecision.ESCALATE
                        state.final_answer = action_input
                        break

                if state.use_reflection:
                    from cognitive.reflector import Reflector
                    # Reuses the `brain` already passed into _loop() rather
                    # than constructing another BrainLLM — same fix as
                    # above, same root cause (record.md Session 9).
                    reflection = await Reflector().reflect(state.goal, action_input, brain)
                    state.reflections.append(reflection)
                    state.add_step(StepType.REFLECT,
                        f"satisfied={reflection.satisfied} confidence={reflection.confidence}"
                        + (f" issues={reflection.issues}" if reflection.issues else ""))
                    await self._emit(state.steps[-1])
                    if not reflection.satisfied and not reflection.parse_failed:
                        state.reflection_correction_attempts += 1
                        if state.reflection_correction_attempts <= 2:
                            messages.append({"role": "user", "content":
                                f"[SELF-CRITIQUE] On reflection, this answer may not fully satisfy the goal: "
                                f"{'; '.join(reflection.issues)}. Reconsider and respond again with mark_complete "
                                f"(or continue working if more steps are needed)."})
                            continue
                        # Exhausted reflection-driven correction attempts — same
                        # pattern as the schema-validation exhaustion above:
                        # surface to a human rather than looping forever on a
                        # critique that itself might be wrong.
                        state.add_step(StepType.HITL,
                            f"Reflection flagged unresolved issues after {state.reflection_correction_attempts} "
                            f"attempts: {'; '.join(reflection.issues)}")
                        await self._emit(state.steps[-1])
                        state.decision = LoopDecision.ESCALATE
                        state.final_answer = action_input
                        break

                state.add_step(StepType.DONE, action_input)
                await self._emit(state.steps[-1])
                state.decision = LoopDecision.COMPLETE
                state.final_answer = action_input
                break

            if action == "ask_user":
                state.add_step(StepType.HITL, action_input)
                await self._emit(state.steps[-1])
                state.decision = LoopDecision.ESCALATE
                state.final_answer = action_input
                break

            # Memory recall
            if action == "recall_memory":
                mems = await self.memory.recall(self.user_id, action_input, limit=5)
                text = "\n".join(f"- {m.get('content','')}" for m in mems) or "None found."
                state.add_step(StepType.OBSERVE, text[:200])
                await self._emit(state.steps[-1])
                messages.append({"role": "user", "content": f"[Memory]\n{text}\n\nWhat next?"})
                continue

            # Web search
            if action == "search_web":
                from execution.search import search_web
                state.add_step(StepType.THINK, f"Searching: {action_input[:80]}")
                await self._emit(state.steps[-1])
                result = await search_web(action_input)
                answer = result.get("answer", "")
                snippets = "\n".join(f"• {r['title']}: {r['content'][:200]}"
                                     for r in result.get("results", [])[:3])
                obs_text = f"Answer: {answer}\n\nSnippets:\n{snippets}"
                state.add_step(StepType.OBSERVE, obs_text[:300])
                await self._emit(state.steps[-1])
                messages.append({"role": "user", "content": f"[Search]\n{obs_text}\n\nWhat next?"})
                self.obs.record_tool_call(state.id, self.agent.agent_id, self.session_id,
                                          "search_web", action_input, obs_text[:300],
                                          "success", ucip.decision, latency_ms,
                                          iteration=state.iteration)
                continue

            # Code execution
            if action in ("write_python", "write_bash", "write_node"):
                lang = {"write_python":"python","write_bash":"bash","write_node":"node"}[action]
                state.add_step(StepType.WRITE, action_input,
                                language=lang, description=description)
                await self._emit(state.steps[-1])
                state.scripts_written.append({"language":lang,"code":action_input,
                                               "description":description,"iteration":state.iteration})

                exec_step = state.add_step(StepType.EXECUTE, f"Running {lang}…")
                await self._emit(exec_step)

                exec_t0 = time.perf_counter_ns()
                result  = await self.sandbox.run(code=action_input, language=lang,
                                                  run_id=f"{state.id}-{state.iteration}",
                                                  timeout=30)
                exec_ms = (time.perf_counter_ns() - exec_t0) // 1_000_000
                state.total_latency_ms += exec_ms
                state.execution_results.append(result.to_dict())
                if result.sandbox_violations:
                    state.sandbox_violations.extend(result.sandbox_violations)

                exec_step.content = f"exit={result.exit_code} ({exec_ms}ms) {result.status}"
                exec_step.metadata = {**result.to_dict(), "exec_ms": exec_ms}
                await self._emit(exec_step)

                self.obs.record_tool_call(state.id, self.agent.agent_id, self.session_id,
                                          action, action_input[:300],
                                          result.to_brain_summary()[:400],
                                          result.status, ucip.decision, exec_ms,
                                          error_type="timeout" if result.status=="timeout" else
                                                     ("sandbox_denied" if result.status=="sandbox_denied" else
                                                     (None if result.exit_code==0 else "runtime_error")),
                                          iteration=state.iteration)

                self.gateway.record_result(action, result.status == "success")
                self.ckpt.save(state.id, {"goal":state.goal,"iteration":state.iteration,
                                           "session_id":self.session_id,"user_id":self.user_id})

                obs_text = result.to_brain_summary()
                state.add_step(StepType.OBSERVE, obs_text[:400],
                                status=result.status, exit_code=result.exit_code,
                                violations=result.sandbox_violations)
                await self._emit(state.steps[-1])
                messages.append({"role":"user","content":f"[Result]\n{obs_text}\n\nWhat next?"})
                continue

        await self.memory.save(self.user_id, "user", state.goal,
                               session_id=self.session_id)
        await self.memory.save(self.user_id, "assistant", state.final_answer,
                               session_id=self.session_id,
                               metadata={"loop_id":state.id,"iterations":state.iteration,
                                         "decision":str(state.decision)})

    async def _emit(self, step):
        if self.on_step:
            try:
                if asyncio.iscoroutinefunction(self.on_step):
                    await self.on_step(step)
                else:
                    self.on_step(step)
            except Exception as e:
                logger.debug(f"Emit error: {e}")
