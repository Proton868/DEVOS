"""Bounded LLM planner: contract, governance, no execution authority."""
from __future__ import annotations

import json
import os

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_agentic_llm.db")

import pytest

from brain.agentic_llm_planner import (
    ALLOWED_ACTION_TYPES,
    FakeLLMProvider,
    PlannerValidationError,
    build_planner_context,
    make_llm_planner,
    parse_structured_plan,
    planner_has_no_execution_authority,
    validate_plan_against_context,
)
from brain.agentic_runtime import TurnDecision, default_planner


def test_valid_structured_plan_accepted():
    p = parse_structured_plan({
        "action": {
            "type": "capability_request",
            "capability": "ucip:filesystem.read",
            "input": {"path": "a.py"},
            "rationale": "read file",
        }
    })
    assert p.action_type == "capability_request"
    assert p.capability == "ucip:filesystem.read"
    d = p.to_turn_decision()
    assert d.kind == "capability_request"
    assert d.capability_id == "ucip:filesystem.read"


def test_malformed_json_rejected():
    with pytest.raises(PlannerValidationError, match="malformed_json"):
        parse_structured_plan("{not json")


def test_unknown_action_rejected():
    with pytest.raises(PlannerValidationError, match="unknown_action"):
        parse_structured_plan({"action": {"type": "launch_missiles"}})


def test_unknown_capability_rejected_against_context():
    plan = parse_structured_plan({
        "action": {"type": "capability_request", "capability": "not.real.cap", "input": {}},
    })
    with pytest.raises(PlannerValidationError, match="unknown_capability"):
        validate_plan_against_context(
            plan, {"allowed_capabilities": ["ucip:filesystem.read"]},
        )


def test_free_form_done_cannot_complete():
    with pytest.raises(PlannerValidationError, match="free_form_complete"):
        parse_structured_plan("done")
    with pytest.raises(PlannerValidationError, match="free_form_complete"):
        parse_structured_plan("finished")


def test_shell_code_request_rejected():
    with pytest.raises(PlannerValidationError):
        parse_structured_plan({
            "action": {"type": "capability_request", "capability": "shell:bash", "input": {}},
        })
    with pytest.raises(PlannerValidationError):
        parse_structured_plan({
            "action": {"type": "capability_request", "capability": "python", "input": {"code": "1+1"}},
        })


def test_forbidden_input_keys_rejected():
    with pytest.raises(PlannerValidationError, match="forbidden_input_key"):
        parse_structured_plan({
            "action": {
                "type": "capability_request",
                "capability": "ucip:filesystem.read",
                "input": {"grant": {"all": True}},
            },
        })


def test_cannot_override_bounds_via_input():
    plan = parse_structured_plan({
        "action": {
            "type": "capability_request",
            "capability": "ucip:filesystem.read",
            "input": {"max_turns": 9999},
        },
    })
    with pytest.raises(PlannerValidationError, match="cannot_override_bounds"):
        validate_plan_against_context(
            plan, {"allowed_capabilities": ["ucip:filesystem.read"]},
        )


def test_fake_provider_scripted_plan():
    prov = FakeLLMProvider(script=[
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "ucip:filesystem.read",
                "input": {},
            }
        }),
    ])
    planner = make_llm_planner(prov)
    d = planner({
        "allowed_capabilities": ["ucip:filesystem.read"],
        "turn": 0,
    })
    assert d.kind == "capability_request"
    assert d.capability_id == "ucip:filesystem.read"


def test_provider_exception_becomes_block():
    prov = FakeLLMProvider(raise_exc=TimeoutError("provider timeout"))
    planner = make_llm_planner(prov)
    d = planner({"allowed_capabilities": ["ucip:filesystem.read"]})
    assert d.kind == "block"
    assert "planner_provider_error" in d.reason


def test_malformed_provider_output_no_execution():
    prov = FakeLLMProvider(script=["this is not a plan"])
    planner = make_llm_planner(prov)
    d = planner({"allowed_capabilities": ["x"]})
    assert d.kind == "block"
    assert "planner_invalid" in d.reason


def test_structured_complete_still_not_evidence():
    """type=complete is a decision only — CompletionContract enforced by runtime."""
    p = parse_structured_plan({
        "action": {"type": "complete", "rationale": "I finished"},
    })
    d = p.to_turn_decision()
    assert d.kind == "complete"
    assert d.complete is True
    # Fake claim is not evidence
    assert "evidence" not in (d.inputs or {})


def test_planner_context_scrubs_secrets():
    ctx = build_planner_context({
        "task_id": "t1",
        "objective": "do work",
        "api_key": "SECRET_KEY_VALUE",
        "password": "hunter2",
        "allowed_capabilities": ["ucip:filesystem.read"],
        "grants": [{"all": True}],
        "turn": 1,
    })
    blob = json.dumps(ctx)
    assert "SECRET_KEY_VALUE" not in blob
    assert "hunter2" not in blob
    assert "grants" not in ctx


def test_planner_has_no_execution_authority_guard():
    assert planner_has_no_execution_authority() is True


def test_default_planner_still_works():
    d = default_planner({
        "allowed_capabilities": ["ucip:filesystem.read"],
        "turn": 0,
        "last_observation": None,
    })
    assert d.kind == "capability_request"


def test_llm_plan_then_complete_script():
    prov = FakeLLMProvider(script=[
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "ucip:filesystem.read",
                "input": {"path": "x"},
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "structured"}}),
    ])
    planner = make_llm_planner(prov)
    d1 = planner({"allowed_capabilities": ["ucip:filesystem.read"], "turn": 0})
    assert d1.kind == "capability_request"
    d2 = planner({
        "allowed_capabilities": ["ucip:filesystem.read"],
        "turn": 1,
        "last_observation": {"status": "executed"},
    })
    assert d2.kind == "complete"


def test_allowed_action_types_closed():
    assert "capability_request" in ALLOWED_ACTION_TYPES
    assert "shell" not in ALLOWED_ACTION_TYPES


@pytest.mark.skipif(os.environ.get("DEVOS_LLM_SMOKE") != "1", reason="DEVOS_LLM_SMOKE not set")
def test_smoke_real_provider_optional():
    """Optional smoke — never runs in ordinary CI."""
    from brain.agentic_llm_planner import BrainLLMProvider, make_llm_planner
    planner = make_llm_planner(BrainLLMProvider())
    d = planner({
        "objective": "reply with complete only",
        "allowed_capabilities": [],
        "turn": 0,
    })
    assert d.kind in ("complete", "block", "fail", "capability_request")
