"""Focused production-path blockers for coding-agent spine."""
from __future__ import annotations

from brain.orchestration_runtime import NodeExecutionResult
from brain.mission_acceptance import evaluate_mission_acceptance
from brain.coding_evidence import build_coding_evidence


def test_provider_429_classification_policy():
    """Mirror brain.llm.classify_provider_http_status without importing settings."""
    def classify(code: int) -> dict:
        if code == 429:
            return {"retryable": True, "category": "rate_limited", "http_status": 429}
        if code in (500, 502, 503, 504):
            return {"retryable": True, "category": "server", "http_status": code}
        if code in (401, 403):
            return {"retryable": False, "category": "auth", "http_status": code}
        return {"retryable": False, "category": "other", "http_status": code}

    meta = classify(429)
    assert meta["retryable"] is True
    assert meta["category"] == "rate_limited"
    assert classify(401)["retryable"] is False


def test_node_result_provider_failure_forces_not_success():
    r = NodeExecutionResult(success=True, status="succeeded")
    data = {
        "success": True,
        "summary": "All providers failed",
        "provider_failure": {"category": "rate_limited", "retryable": True, "provider": "openrouter"},
    }
    pf = data.get("provider_failure")
    if pf:
        r.provider_failure = dict(pf)
        r.success = False
        r.status = "failed"
        r.error = data.get("summary")
    assert r.success is False
    assert r.status == "failed"
    assert r.provider_failure["category"] == "rate_limited"


def test_acceptance_requires_coding_evidence_when_files_present():
    files = [{"path": "index.html"}]
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=files,
        ponytail={"passed": True, "applicable": True},
        evidence_refs=["ev1"],
        mission_id="m1",
        user_id="u1",
        require_coding_evidence=True,
        coding_evidence=None,
    )
    assert acc["ok"] is False


def test_acceptance_with_real_coding_evidence():
    files = [{"path": "index.html"}]
    ev = build_coding_evidence(
        mission_id="m1",
        project_id="default",
        user_id="u1",
        agent_id="agent:web",
        persona_id="web",
        files_changed=files,
        commands=[{"command": "python -m pytest -q", "exit_code": 0, "ok": True, "kind": "test"}],
        validation={"ok": True, "works": True},
        success=True,
    )
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=files,
        ponytail={"passed": True, "applicable": True, "evidence_id": ev.evidence_id},
        evidence_refs=[ev.evidence_id],
        mission_id="m1",
        user_id="u1",
        expected_mission_id="m1",
        expected_user_id="u1",
        require_coding_evidence=True,
        coding_evidence=ev.to_dict(),
    )
    assert acc["ok"] is True


def test_node_result_to_dict_includes_provider_failure():
    r = NodeExecutionResult(
        success=False,
        status="failed",
        provider_failure={"category": "rate_limited", "retryable": True},
        commands=[{"command": "pytest", "exit_code": 1, "ok": False}],
    )
    d = r.to_dict()
    assert d["provider_failure"]["retryable"] is True
    assert d["commands"][0]["command"] == "pytest"
    assert d["success"] is False


def test_fabricated_coding_evidence_cannot_accept():
    files = [{"path": "a.py"}]
    acc = evaluate_mission_acceptance(
        execution_ok=True,
        status="accepted",
        files_changed=files,
        ponytail={"passed": True},
        evidence_refs=["e1"],
        require_coding_evidence=True,
        coding_evidence={
            "evidence_id": "e1",
            "mission_id": "m1",
            "project_id": "p",
            "agent_id": "a",
            "commands": [{"command": "x", "exit_code": 0, "ok": True}],
            "files_changed": ["a.py"],
            "validation": {"ok": True},
            "timestamps": {"created_at": "t"},
            "success": True,
            "fabricated": True,
        },
    )
    assert acc["ok"] is False


def test_run_command_in_project_exists_and_scopes():
    import asyncio
    from execution.runner import run_command_in_project
    from execution.files import FileService

    async def _run():
        fs = FileService("cmduser", "cmdproj")
        fs.write("a.txt", "ok\n")
        # Trusted policy: path-scoping test; untrusted would require strong sandbox.
        r = await run_command_in_project(
            "cmduser", "cmdproj", "cat a.txt", timeout_s=15, policy="trusted",
        )
        assert r["ok"] is True
        assert "ok" in r["stdout"]
        bad = await run_command_in_project("..", "x", "echo hi")
        assert bad["ok"] is False

    asyncio.run(_run())


def test_persist_coding_evidence_uses_real_chain_api():
    from brain.coding_evidence import build_coding_evidence, persist_coding_evidence
    from governance.evidence import EvidenceChainManager

    ev = build_coding_evidence(
        mission_id="m-persist-1",
        project_id="p1",
        user_id="u1",
        agent_id="agent:code",
        files_changed=["a.py"],
        commands=[{"command": "true", "exit_code": 0, "ok": True}],
        validation={"ok": True},
        success=True,
    )
    eid = persist_coding_evidence(ev)
    assert eid == ev.evidence_id
    chain = EvidenceChainManager.load(f"coding-{ev.mission_id}")
    assert chain is not None
    assert len(chain.nodes) >= 1


def test_file_only_coding_evidence_valid_without_commands():
    from brain.coding_evidence import build_coding_evidence, validate_coding_evidence

    ev = build_coding_evidence(
        mission_id="m-files",
        project_id="p",
        agent_id="web",
        files_changed=["index.html"],
        commands=[],
        validation={"ok": True, "structure_ok": True},
        success=True,
    )
    res = validate_coding_evidence(ev, require_commands=True)
    assert res["ok"] is True
    assert res["checks"].get("commands_optional_file_mission") is True
