"""Nuha orchestration state machine and plan mode (no network)."""
import pytest
from brain.orchestration import (
    OrchStatus,
    can_transition,
    transition,
    detect_mode,
    NuhaMode,
    OrchestrationPlan,
    _heuristic_steps,
    TERMINAL,
)


def test_detect_chat():
    assert detect_mode("What is the best way to structure the agent runtime?") == NuhaMode.CHAT


def test_detect_plan():
    assert detect_mode("Plan a one-page shoe website.") == NuhaMode.PLAN


def test_detect_action():
    assert detect_mode("Build it.") == NuhaMode.ACTION
    assert detect_mode("Create the website about shoes") == NuhaMode.ACTION


def test_transitions_planning_path():
    s = OrchStatus.IDLE
    for nxt in (
        OrchStatus.INTENT_DETECTED,
        OrchStatus.PLANNING,
        OrchStatus.CONTEXT_GATHERING,
        OrchStatus.GOAL_ANALYSIS,
        OrchStatus.TASK_DECOMPOSITION,
        OrchStatus.PERSONA_SELECTION,
        OrchStatus.CAPABILITY_ANALYSIS,
        OrchStatus.DEPENDENCY_ANALYSIS,
        OrchStatus.RISK_ANALYSIS,
        OrchStatus.VERIFICATION_DESIGN,
        OrchStatus.PLAN_READY,
    ):
        assert can_transition(s, nxt)
        s = transition(s, nxt)
    assert s == OrchStatus.PLAN_READY


def test_invalid_transition_raises():
    with pytest.raises(ValueError):
        transition(OrchStatus.IDLE, OrchStatus.COMPLETED)


def test_plan_ready_does_not_auto_execute():
    assert OrchStatus.RUNNING not in (
        t for t in __import__("brain.orchestration", fromlist=["_TRANSITIONS"])._TRANSITIONS[OrchStatus.PLAN_READY]
    )
    # Must go ACTION_REQUESTED first
    assert can_transition(OrchStatus.PLAN_READY, OrchStatus.ACTION_REQUESTED)


def test_heuristic_shoe_website_steps():
    steps = _heuristic_steps("Plan a one-page shoe website")
    assert len(steps) >= 2
    personas = {s.persona_id for s in steps}
    assert "web" in personas or "design" in personas
    for s in steps:
        assert s.required_capabilities
        # Caps listed are not grants — just presence of analysis
        assert isinstance(s.required_capabilities, list)


def test_terminal_states():
    assert OrchStatus.COMPLETED in TERMINAL
    assert OrchStatus.CANCELLED in TERMINAL
    assert OrchStatus.BLOCKED in TERMINAL
