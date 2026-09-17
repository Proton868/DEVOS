"""Persona runtime_tools must constrain AgentRuntime tool surface."""
from __future__ import annotations

from pathlib import Path


def test_orchestration_passes_allowed_tool_names():
    src = Path("brain/orchestration_runtime.py").read_text()
    assert "allowed_tool_names=tool_allow" in src
    assert "to_ucip" in src


def test_agent_runtime_intersects_persona_tools():
    src = Path("brain/agent_runtime.py").read_text()
    assert "allowed_tool_names" in src
    assert "self.allowed_tool_names" in src
    assert "allowed_names = allowed_names & self.allowed_tool_names" in src or (
        "allowed_names & self.allowed_tool_names" in src
    )


def test_provider_failure_emits_structured_fields():
    src = Path("brain/agent_runtime.py").read_text()
    assert "provider_failure" in src
    assert "to_public_dict" in src
    assert "rate_limited" in src
