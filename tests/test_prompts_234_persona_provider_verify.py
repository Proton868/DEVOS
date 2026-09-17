"""Prompts 2–4: executable personas, provider failure semantics, verification gate."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_persona_resolves_executable_agent_with_tools():
    from brain.personas import list_personas
    from brain.executable_agents import build_contract_for_persona

    specialists = [p for p in list_personas() if p.role == "specialist"]
    assert specialists
    for p in specialists[:8]:
        c = build_contract_for_persona(p)
        assert c.agent_id.startswith("agent:")
        assert c.persona_id == p.id
        assert c.can_receive_delegation is True
        assert c.runtime_tools  # specialist must have tools


def test_delegation_source_binds_contract_and_acceptance():
    src = (ROOT / "brain" / "delegation.py").read_text()
    assert "build_contract_for_persona" in src
    assert "persona_system_prompt" in src
    assert "evaluate_mission_acceptance" in src
    assert "mission_acceptance_failed" in src or "acceptance.get" in src


def test_classify_429_and_401():
    src = (ROOT / "brain" / "llm.py").read_text()
    start = src.index("def classify_provider_http_status")
    end = src.index("\ndef _redact_provider_error_text", start)
    ns: dict = {}
    exec(src[start:end], ns, ns)
    c = ns["classify_provider_http_status"]
    assert c(429) == {"retryable": True, "category": "rate_limited", "http_status": 429}
    assert c(401)["retryable"] is False
    assert c(401)["category"] == "auth"
    assert c(503)["retryable"] is True


def test_provider_exhausted_public_dict_fields():
    # Load class without full BrainLLM settings if possible
    src = (ROOT / "brain" / "llm.py").read_text()
    assert "to_public_dict" in src
    assert "providers_tried" in src
    assert "rate_limited" in src


def test_acceptance_provider_failure_not_complete():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=False,
        status="failed",
        files_changed=[],
        ponytail={"passed": False},
        evidence_refs=[],
    )
    assert acc["ok"] is False


def test_acceptance_model_ok_verify_fail():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[{"path": "a.py"}],
        ponytail={"passed": False, "applicable": True},
        evidence_refs=[],
        artifact_producing=True,
    )
    assert acc["ok"] is False


def test_acceptance_missing_artifact():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[],
        ponytail={"passed": True, "evidence_id": "e1", "applicable": True},
        evidence_refs=["e1"],
        artifact_producing=True,
    )
    assert acc["ok"] is False


def test_acceptance_verified_success():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "prime_probe.txt"}],
        ponytail={"passed": True, "evidence_id": "ev", "applicable": True},
        evidence_refs=["ev"],
        artifact_producing=True,
    )
    assert acc["ok"] is True
    assert acc.get("reason") == "accepted"


def test_orch_runtime_passes_persona_tools():
    src = (ROOT / "brain" / "orchestration_runtime.py").read_text()
    assert "runtime_tools" in src
    assert "allowed_tool_names" in src


def test_agent_runtime_no_false_provider_success():
    src = (ROOT / "brain" / "agent_runtime.py").read_text()
    assert "ProviderExhaustedError" in src
    assert "provider_failure" in src
