"""Local contracts for real specialist runtime path (not live Prime proof)."""
from __future__ import annotations

from pathlib import Path


def test_orchestration_runtime_source_uses_agent_runtime_and_default_provider():
    src = Path("brain/orchestration_runtime.py").read_text()
    assert "AgentRuntime(" in src
    assert "DEVOS_ORCH_FAKE_RUNTIME" in src
    assert "DEFAULT_PROVIDER" in src
    assert "omniroute" in src
    assert "DEVOS_ALLOW_FAKE_RUNTIME" in src


def test_fake_runtime_guard_present_in_source():
    src = Path("brain/orchestration_runtime.py").read_text()
    assert "AGENT_RUNTIME_UNAVAILABLE: DEVOS_ORCH_FAKE_RUNTIME set without test allow" in src
    assert "Never silently fall back in production" in src or "TEST-ONLY" in src


def test_delegation_calls_run_node_on_agent_runtime():
    src = Path("brain/delegation.py").read_text()
    assert "_run_agent_node" in src
    assert "run_node_on_agent_runtime" in src
    assert "on_progress" in src


def test_agent_runtime_uses_brain_llm():
    src = Path("brain/agent_runtime.py").read_text()
    assert "BrainLLM" in src
    assert "stream_chat" in src
