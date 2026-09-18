"""Durable automation orchestration — control flow without a second executor.

The orchestrator decides *which* step is eligible.
The automation runtime / workflow_executor *executes* steps.

Condition language (fail-closed, no eval/exec):
  - literals: true/false/1/0/yes/no
  - path equality: step_id.key == value  |  step_id.key != value
  - bare path truthiness: step_id.key
  Paths resolve against execution context (prior step outputs).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from brain.workflow import Workflow, WorkflowStep, StepType
from brain.workflow_executor import (
    ExecutionState,
    STEP_SUCCEEDED,
    STEP_FAILED,
    STEP_DENIED,
    STEP_UNKNOWN,
    STEP_SKIPPED,
    STEP_RUNNING,
    _eval_condition,
    _workflow_from_snapshot,
    _step_map,
)

logger = logging.getLogger("devos.automation_orchestration")


class EdgeOn(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TRUE = "true"
    FALSE = "false"
    ALWAYS = "always"


class JoinMode(str, Enum):
    ALL_SUCCESS = "all_success"
    ANY_SUCCESS = "any_success"


@dataclass(frozen=True)
class WorkflowEdge:
    source: str
    target: str
    on: str = EdgeOn.SUCCESS.value  # success | failure | true | false | always

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "on": self.on}


@dataclass
class JoinSpec:
    target: str
    deps: tuple[str, ...]
    mode: str = JoinMode.ALL_SUCCESS.value

    def to_dict(self) -> dict:
        return {"target": self.target, "deps": list(self.deps), "mode": self.mode}


def _edges_from_steps(steps: dict[str, WorkflowStep]) -> list[WorkflowEdge]:
    """Derive edges from classic next_step / on_error / branches."""
    edges: list[WorkflowEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(src: str, tgt: Optional[str], on: str) -> None:
        if not tgt:
            return
        key = (src, tgt, on)
        if key in seen:
            return
        seen.add(key)
        edges.append(WorkflowEdge(source=src, target=tgt, on=on))

    for sid, step in sorted(steps.items()):  # deterministic
        if step.next_step:
            add(sid, step.next_step, EdgeOn.SUCCESS.value)
        if step.on_error:
            add(sid, step.on_error, EdgeOn.FAILURE.value)
        for bkey, bid in sorted((step.branches or {}).items()):
            bk = str(bkey).lower()
            if bk in ("true", "1", "yes"):
                add(sid, bid, EdgeOn.TRUE.value)
            elif bk in ("false", "0", "no"):
                add(sid, bid, EdgeOn.FALSE.value)
            else:
                add(sid, bid, EdgeOn.SUCCESS.value)
    return edges


def _edges_from_definition(definition: dict, steps: dict[str, WorkflowStep]) -> list[WorkflowEdge]:
    edges = _edges_from_steps(steps)
    raw = (definition.get("edges") or definition.get("metadata", {}).get("edges") or [])
    seen = {(e.source, e.target, e.on) for e in edges}
    for item in raw:
        if not isinstance(item, dict):
            continue
        src, tgt = item.get("source"), item.get("target")
        on = str(item.get("on") or item.get("condition") or EdgeOn.SUCCESS.value).lower()
        if not src or not tgt:
            continue
        key = (str(src), str(tgt), on)
        if key in seen:
            continue
        seen.add(key)
        edges.append(WorkflowEdge(source=str(src), target=str(tgt), on=on))
    return edges


def _joins_from_definition(definition: dict, steps: dict[str, WorkflowStep]) -> list[JoinSpec]:
    joins: list[JoinSpec] = []
    raw = definition.get("joins") or definition.get("metadata", {}).get("joins") or []
    for item in raw:
        if not isinstance(item, dict):
            continue
        target = item.get("target")
        deps = item.get("deps") or item.get("depends_on") or []
        mode = str(item.get("mode") or JoinMode.ALL_SUCCESS.value).lower()
        if target and deps:
            joins.append(JoinSpec(target=str(target), deps=tuple(str(d) for d in deps), mode=mode))
    # Also from step.metadata.depends_on
    for sid, step in sorted(steps.items()):
        meta = step.metadata or {}
        deps = meta.get("depends_on") or meta.get("deps")
        if deps:
            mode = str(meta.get("join") or meta.get("join_mode") or JoinMode.ALL_SUCCESS.value).lower()
            joins.append(JoinSpec(target=sid, deps=tuple(str(d) for d in deps), mode=mode))
    return joins


def _status_of(state: ExecutionState, step_id: str) -> Optional[str]:
    rec = (state.records or {}).get(step_id) or {}
    return rec.get("status")


def _succeeded(state: ExecutionState, step_id: str) -> bool:
    return step_id in (state.completed or []) or _status_of(state, step_id) == STEP_SUCCEEDED


def _failed(state: ExecutionState, step_id: str) -> bool:
    return _status_of(state, step_id) in (STEP_FAILED, STEP_DENIED)


def _unknown(state: ExecutionState, step_id: str) -> bool:
    return _status_of(state, step_id) == STEP_UNKNOWN


def has_unresolved_unknown(state: ExecutionState) -> bool:
    for sid, rec in (state.records or {}).items():
        if isinstance(rec, dict) and rec.get("status") == STEP_UNKNOWN:
            return True
    return False


def evaluate_condition_expr(expr: Optional[str], context: dict) -> tuple[bool, Optional[str]]:
    """Public bounded condition evaluation — delegates to fail-closed executor helper."""
    return _eval_condition(expr, context)


def select_eligible_steps(
    snap: dict,
    state: ExecutionState,
) -> list[str]:
    """Deterministically list step IDs eligible to run next.

    Empty list means: terminal, waiting for join, paused on UNKNOWN, or no work.
    """
    if has_unresolved_unknown(state):
        return []  # pause — no consequential advancement

    # terminal_status is advisory for the job loop; eligibility is derived from
    # step records so partial sequential orchestration can continue a join graph.

    wf = _workflow_from_snapshot(snap)
    steps = _step_map(wf)
    if not steps:
        return []

    definition = dict(snap.get("definition") or {})
    edges = _edges_from_definition(definition, steps)
    joins = {j.target: j for j in _joins_from_definition(definition, steps)}

    # Done steps (succeeded or skipped terminal)
    done = set(state.completed or [])
    for sid, rec in (state.records or {}).items():
        if isinstance(rec, dict) and rec.get("status") in (STEP_SKIPPED,):
            done.add(sid)

    # Start candidates: no inbound edges among defined steps
    targets = {e.target for e in edges}
    roots = sorted(sid for sid in steps if sid not in targets)
    if not roots and wf.start_step:
        roots = [wf.start_step]
    if not roots:
        roots = sorted(steps.keys())[:1]

    eligible: list[str] = []

    def join_ok(sid: str) -> bool:
        j = joins.get(sid)
        if not j:
            return True
        if j.mode == JoinMode.ANY_SUCCESS.value:
            return any(_succeeded(state, d) for d in j.deps)
        # default ALL_SUCCESS
        return all(_succeeded(state, d) for d in j.deps)

    # Already finished steps are never eligible
    finished = set(done)
    for sid, rec in (state.records or {}).items():
        if isinstance(rec, dict) and rec.get("status") in (
            STEP_SUCCEEDED, STEP_FAILED, STEP_DENIED, STEP_UNKNOWN, STEP_SKIPPED
        ):
            finished.add(sid)

    # Roots if not finished
    for sid in roots:
        if sid not in finished and join_ok(sid):
            # root with no deps is eligible only if nothing else required
            inbound = [e for e in edges if e.target == sid]
            if not inbound and sid not in finished:
                eligible.append(sid)

    # Edge-activated targets
    for e in sorted(edges, key=lambda x: (x.source, x.target, x.on)):
        if e.target in finished or e.target in eligible:
            continue
        if e.target not in steps:
            continue
        src_st = _status_of(state, e.source)
        activated = False
        if e.on == EdgeOn.SUCCESS.value and _succeeded(state, e.source):
            activated = True
        elif e.on == EdgeOn.FAILURE.value and _failed(state, e.source):
            activated = True
        elif e.on == EdgeOn.TRUE.value and _succeeded(state, e.source):
            # condition step recorded outputs.result
            out = ((state.context or {}).get(e.source) or {})
            if isinstance(out, dict) and "result" in out:
                activated = bool(out.get("result"))
            else:
                activated = True  # non-condition success + true edge
        elif e.on == EdgeOn.FALSE.value and _succeeded(state, e.source):
            out = ((state.context or {}).get(e.source) or {})
            if isinstance(out, dict) and "result" in out:
                activated = not bool(out.get("result"))
            else:
                activated = False
        elif e.on == EdgeOn.ALWAYS.value and e.source in finished:
            activated = True
        if activated and join_ok(e.target):
            eligible.append(e.target)

    # Join-only nodes (no activating edge yet but deps met)
    for sid, j in sorted(joins.items()):
        if sid in finished or sid in eligible:
            continue
        if sid not in steps:
            continue
        if join_ok(sid):
            eligible.append(sid)

    # Deterministic unique order
    seen: set[str] = set()
    ordered: list[str] = []
    for sid in sorted(set(eligible)):
        if sid not in seen and sid not in finished:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def select_next_step(snap: dict, state: ExecutionState) -> Optional[str]:
    """Single next step (first eligible) for sequential runtime."""
    elig = select_eligible_steps(snap, state)
    return elig[0] if elig else None


def record_branch_decision(state: ExecutionState, source: str, target: Optional[str], reason: str) -> None:
    """Persist branch decision in execution state for restart determinism."""
    decisions = dict(state.context.get("_branch_decisions") or {})
    decisions[source] = {"target": target, "reason": reason}
    state.context["_branch_decisions"] = decisions


def condition_language_spec() -> dict:
    return {
        "literals": ["true", "false", "1", "0", "yes", "no"],
        "equality": "path == value | path != value",
        "truthiness": "path",
        "forbidden": ["eval", "exec", "import", "open", "lambda", "__"],
        "resolver": "context paths from prior step outputs",
    }
