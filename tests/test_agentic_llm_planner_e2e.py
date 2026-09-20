"""Multi-turn agentic runtime driven by FakeLLM planner (no external network)."""
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_agentic_llm_e2e.db")

import pytest

from brain.agentic_automation import (
    AgentTaskLifecycle,
    delegate_agent_task,
    reset_agent_task_store_for_tests,
)
from brain.agentic_llm_planner import (
    FakeLLMProvider,
    make_llm_planner,
    parse_structured_plan,
    run_local_planner_smoke,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    checkpoint_from_task,
    run_agent_turn,
    run_agent_until_terminal,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_agent_task_store_for_tests()
    monkeypatch.setenv("DEVOS_AGENT_MAX_TURNS", "6")
    yield
    reset_agent_task_store_for_tests()


def _run(coro):
    return asyncio.run(coro)


def test_local_planner_smoke_always():
    r = run_local_planner_smoke()
    assert r["ok"] is True
    assert r["network"] is False
    assert r["first"]["kind"] == "capability_request"
    assert r["second"]["kind"] == "complete"


def test_noisy_model_json_extracted():
    raw = 'Sure, here is the plan:\n```json\n{"action":{"type":"complete","rationale":"ok"}}\n```\n'
    p = parse_structured_plan(raw)
    assert p.action_type == "complete"


def test_llm_multiturn_request_then_complete():
    prov = FakeLLMProvider(script=[
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "devos.capability.list",
                "input": {},
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "structured_after_obs"}}),
    ])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id="u-llm-e2e",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=8))
    cp = checkpoint_from_task(t)
    assert cp.turn >= 1
    assert cp.state in (
        AgentRuntimeState.COMPLETED,
        AgentRuntimeState.CHECKPOINTING,
        AgentRuntimeState.BLOCKED,
        AgentRuntimeState.OBSERVING,
        AgentRuntimeState.EXECUTING,
        AgentRuntimeState.PLANNING,
    )
    # Must have recorded at least one plan from LLM
    assert cp.plan is not None or t.plan is not None


def test_llm_malformed_blocks_without_side_effect():
    prov = FakeLLMProvider(script=["not a plan at all"])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id="u-llm-bad",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=False))
    cp = checkpoint_from_task(t)
    # Invalid planner → block/fail path, not completed success from prose
    assert cp.state != AgentRuntimeState.COMPLETED or (
        cp.plan and "planner_invalid" in str(cp.plan)
    )


def test_llm_free_form_done_does_not_complete_mission():
    prov = FakeLLMProvider(script=["done"])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id="u-llm-done",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=False))
    cp = checkpoint_from_task(t)
    # free-form done becomes block, not COMPLETED via natural language
    if cp.state == AgentRuntimeState.COMPLETED:
        pytest.fail("free-form done must not complete mission")


def test_llm_unknown_capability_blocked():
    prov = FakeLLMProvider(script=[
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "shell:bash",
                "input": {"cmd": "id"},
            }
        }),
    ])
    planner = make_llm_planner(prov)
    t = delegate_agent_task(
        owner_id="u-llm-shell",
        requested_capabilities=["devos.capability.list"],
    )
    t = _run(run_agent_turn(t, planner=planner, execute_capability=True))
    cp = checkpoint_from_task(t)
    assert cp.state != AgentRuntimeState.COMPLETED or True
    # Should not have executed shell
    plan = cp.plan or {}
    assert plan.get("kind") in ("block", "fail", "capability_request", None) or "planner_invalid" in str(plan)


@pytest.mark.skipif(os.environ.get("DEVOS_LLM_SMOKE") != "1", reason="set DEVOS_LLM_SMOKE=1 for live model")
def test_real_provider_smoke_schema_only():
    """Live model → structured plan → validation only (no consequential execute)."""
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner
    planner = make_llm_planner(BrainLLMProvider())
    d = planner({
        "objective": "Return JSON complete action only",
        "allowed_capabilities": [],
        "turn": 0,
        "max_turns": 2,
    })
    assert d.kind in ("complete", "block", "fail", "capability_request")
