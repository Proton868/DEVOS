"""Every registered persona must satisfy the executable-agent contract."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("REQUIRE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test_personas.db")

ROOT = Path(__file__).resolve().parents[1]


def test_every_persona_has_stable_ids():
    from brain.personas import list_personas
    from brain.executable_agents import build_registry

    personas = list_personas()
    assert len(personas) >= 11, "personas must not be silently removed"
    reg = build_registry()
    assert set(reg.keys()) == {p.id for p in personas}
    for p in personas:
        c = reg[p.id]
        assert c.persona_id == p.id
        assert c.agent_id.startswith("agent:")
        assert c.role in ("orchestrator", "specialist")


def test_every_persona_passes_executable_contract():
    from brain.executable_agents import verify_all_personas

    results = verify_all_personas()
    failures = {k: v for k, v in results.items() if v}
    assert not failures, f"contract violations: {failures}"


def test_capability_matrix_complete():
    from brain.executable_agents import capability_matrix
    from brain.personas import list_personas

    matrix = capability_matrix()
    assert len(matrix) == len(list_personas())
    for row in matrix:
        assert row["capabilities"]
        assert row["workspace_scope"]
        if row["role"] == "specialist":
            assert row["runtime_tools"], row
            assert row["can_receive_delegation"] is True
            # tools must exist in AgentRuntime
            from brain.agent_tools import get_agent_tool

            for t in row["runtime_tools"]:
                assert get_agent_tool(t) is not None, f"missing tool {t} for {row['persona_id']}"
        if row["role"] == "orchestrator":
            assert row["can_delegate"] is True
            assert row["can_receive_delegation"] is False


def test_specialist_tools_are_not_generic_nuha_only():
    """Specialists must not only advertise '*' or empty."""
    from brain.executable_agents import build_registry

    reg = build_registry()
    for pid, c in reg.items():
        if c.role != "specialist":
            continue
        assert "*" not in c.runtime_tools
        assert len(c.runtime_tools) >= 1


def test_code_bearing_personas_require_ponytail():
    from brain.executable_agents import build_registry

    reg = build_registry()
    for pid in ("web", "code", "data", "automation", "design", "writer"):
        assert reg[pid].requires_ponytail is True, pid


def test_no_persona_removed_from_registry():
    """Guard against shrinking the set to make tests pass."""
    from brain.personas import PERSONA_REGISTRY

    expected = {
        "nuha",
        "web",
        "code",
        "design",
        "automation",
        "research",
        "data",
        "business",
        "writer",
        "storyteller",
        "script_writer",
    }
    assert expected.issubset(set(PERSONA_REGISTRY.keys())), (
        f"missing personas: {expected - set(PERSONA_REGISTRY.keys())}"
    )


def test_specialty_policy_for_every_persona():
    from brain.personas import list_personas
    from brain.specialty_policy import SPECIALTY_POLICIES

    for p in list_personas():
        assert p.id in SPECIALTY_POLICIES, f"no specialty policy for {p.id}"
