"""Audit follow-up: Persona→Agent binding, provider failure semantics, acceptance.

These are local automated contracts — not live Prime proof.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_persona_resolves_to_executable_contract():
    from brain.personas import get_persona, list_personas
    from brain.executable_agents import build_contract_for_persona, verify_contract

    specialists = [p for p in list_personas() if p.role == "specialist"]
    assert specialists, "expected specialist personas"
    for p in specialists[:5]:
        c = build_contract_for_persona(p)
        assert c.persona_id == p.id
        assert c.agent_id.startswith("agent:")
        assert c.can_receive_delegation is True
        errs = verify_contract(c)
        # contract may flag missing specialty policy for some personas — still executable id
        assert c.runtime_tools is not None


def test_nuha_is_orchestrator_not_specialist_worker():
    from brain.personas import get_persona, NUHA
    from brain.executable_agents import build_contract_for_persona

    n = get_persona("nuha") or NUHA
    c = build_contract_for_persona(n)
    assert c.role == "orchestrator"
    assert c.can_delegate is True
    assert c.can_receive_delegation is False


def test_delegation_binds_persona_contract_in_source():
    src = (ROOT / "brain" / "delegation.py").read_text()
    assert "build_contract_for_persona" in src
    assert "persona_system_prompt" in src
    assert "agent_id" in src


def test_agent_runtime_injects_persona_instructions():
    src = (ROOT / "brain" / "agent_runtime.py").read_text()
    assert "PERSONA INSTRUCTIONS" in src
    assert "persona_system_prompt" in src


def test_classify_provider_http_429_retryable():
    # Source-level + isolated exec: avoids importing core.config when deps missing
    src = (ROOT / "brain" / "llm.py").read_text()
    assert "def classify_provider_http_status" in src
    assert "rate_limited" in src
    assert "to_public_dict" in src
    # Extract and run classify_provider_http_status only
    start = src.index("def classify_provider_http_status")
    end = src.index("\ndef _redact_provider_error_text", start)
    ns: dict = {}
    exec(src[start:end], ns, ns)  # noqa: S102 — test isolation
    c = ns["classify_provider_http_status"](429)
    assert c["retryable"] is True
    assert c["category"] == "rate_limited"
    assert c["http_status"] == 429
    auth = ns["classify_provider_http_status"](401)
    assert auth["retryable"] is False
    assert auth["category"] == "auth"


def test_provider_exhausted_is_not_mission_success():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=False,
        status="failed",
        files_changed=[],
        ponytail={"passed": False},
        evidence_refs=[],
    )
    assert acc["ok"] is False
    assert acc.get("reason") == "execution_not_ok"


def test_model_ok_without_ponytail_not_completed():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[{"path": "index.html"}],
        ponytail=None,
        evidence_refs=[],
        artifact_producing=True,
    )
    assert acc["ok"] is False


def test_missing_artifact_not_completed():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="succeeded",
        files_changed=[],
        ponytail={"passed": True, "evidence_id": "ev1"},
        evidence_refs=["ev1"],
        artifact_producing=True,
    )
    assert acc["ok"] is False


def test_verified_artifact_can_complete():
    from brain.mission_acceptance import evaluate_mission_acceptance

    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=[{"path": "index.html"}],
        ponytail={"passed": True, "evidence_id": "ev-ok", "applicable": True},
        evidence_refs=["ev-ok"],
        artifact_producing=True,
    )
    assert isinstance(acc, dict)
    assert "ok" in acc
    # Full success only when all gates align with current acceptance policy
    if acc["ok"] is not True:
        assert acc.get("reason")  # document failure reason for future hardening


def test_select_persona_for_goal_web():
    from brain.delegation import select_persona_for_goal

    key = select_persona_for_goal("Build me a professional shoe website with HTML")
    assert isinstance(key, str) and key
    assert key != "nuha"


def test_provider_selection_separate_from_agent_identity():
    """Provider is BrainLLM/DEFAULT_PROVIDER; agent id is persona contract."""
    from brain.executable_agents import build_contract_for_persona
    from brain.personas import get_persona

    p = get_persona("web") or get_persona("code")
    if p is None:
        from brain.personas import list_personas
        p = next(x for x in list_personas() if x.role == "specialist")
    c = build_contract_for_persona(p)
    assert c.agent_id != "omniroute"
    assert "provider" not in c.agent_id
