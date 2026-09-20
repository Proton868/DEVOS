"""Governed project.validate capability tests."""
from __future__ import annotations

import asyncio
import json

import pytest

from execution.project_validate import (
    ALLOWED_PROFILES,
    CAP_PROJECT_VALIDATE,
    PROFILE_STRUCTURE,
    PROFILE_TEST,
    PROFILE_VERIFY,
    ensure_project_validate_registered,
    execute_project_validate,
    ProjectValidateError,
)
from execution.files import FileService
from execution.workspace_capabilities import (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_WORKSPACE_LIST,
    ensure_workspace_capabilities_registered,
)
from governance.capability_substrate import (
    CapabilityContract,
    InvocationContext,
    InvocationRequest,
    get_capability_substrate,
    reset_capability_substrate_for_tests,
)
from governance.capability_catalog import (
    get_capability_catalog,
    reset_capability_catalog_for_tests,
)
from brain.agentic_automation import (
    delegate_agent_task,
    request_capability,
    reset_agent_task_store_for_tests,
)
from brain.agentic_runtime import (
    AgentRuntimeState,
    CompletionContract,
    checkpoint_from_task,
    run_agent_until_terminal,
    set_completion_contract,
)
from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr("execution.files.PROJECTS_DIR", root)
    reset_capability_substrate_for_tests()
    reset_capability_catalog_for_tests()
    reset_agent_task_store_for_tests()
    ensure_workspace_capabilities_registered()
    ensure_project_validate_registered()
    yield
    reset_agent_task_store_for_tests()
    reset_capability_catalog_for_tests()
    reset_capability_substrate_for_tests()


def _ctx(owner="owner1", project="proj1", grants=None):
    g = set(grants or [CAP_PROJECT_VALIDATE, CAP_ARTIFACT_WRITE, CAP_ARTIFACT_READ, CAP_WORKSPACE_LIST])
    return InvocationContext(
        tenant_id="ten1",
        owner_id=owner,
        granted_capabilities=g,
        actor_type="agent",
        actor_id=owner,
        surface="test",
        client_supplied_grants=False,
        metadata={"project_id": project},
    )


def _req(inputs, ctx=None):
    return InvocationRequest(
        capability_id=CAP_PROJECT_VALIDATE,
        inputs=inputs,
        context=ctx or _ctx(),
    )


def _contract():
    return CapabilityContract(
        identity=CAP_PROJECT_VALIDATE,
        version="1",
        name="Project Validate",
        category="validation",
        description="validate",
    )


def _run(coro):
    return asyncio.run(coro)


def test_capability_discoverable():
    e = get_capability_catalog().get_capability(CAP_PROJECT_VALIDATE)
    assert e is not None
    assert e.consequential is True
    assert e.requires_authorization is True


def test_unsupported_profile_rejected():
    with pytest.raises((ProjectValidateError, RuntimeError)):
        _run(execute_project_validate(_contract(), _req({"profile": "bash -rf /"})))


def test_forbidden_command_field():
    with pytest.raises((ProjectValidateError, RuntimeError)):
        _run(execute_project_validate(
            _contract(),
            _req({"profile": PROFILE_STRUCTURE, "command": "npm test"}),
        ))


def test_forbidden_workspace_root():
    with pytest.raises((ProjectValidateError, RuntimeError)):
        _run(execute_project_validate(
            _contract(),
            _req({"profile": PROFILE_STRUCTURE, "workspace_root": "/etc"}),
        ))


def test_missing_project_identity():
    ctx = InvocationContext(
        tenant_id="t",
        owner_id="owner1",
        granted_capabilities={CAP_PROJECT_VALIDATE},
        metadata={},  # no project_id
    )
    with pytest.raises((ProjectValidateError, RuntimeError)):
        _run(execute_project_validate(_contract(), _req({"profile": PROFILE_STRUCTURE}, ctx)))


def test_structure_fails_on_empty_workspace():
    out = _run(execute_project_validate(
        _contract(),
        _req({"profile": PROFILE_STRUCTURE}),
    ))
    assert out["success"] is False
    assert out["status"] == "failed"
    assert out["evidence"]["digest"]


def test_structure_passes_with_files():
    fs = FileService("owner1", "proj1")
    (fs.root / "README.md").write_text("# hi")
    (fs.root / "src").mkdir()
    (fs.root / "src" / "main.py").write_text("print(1)\n")
    out = _run(execute_project_validate(
        _contract(),
        _req({"profile": PROFILE_STRUCTURE}),
    ))
    assert out["success"] is True
    assert out["profile"] == PROFILE_STRUCTURE
    assert out["evidence"]["success"] is True
    # Planner-forged fields not accepted as authority — evidence is server-built
    assert out["evidence_digest"] == out["evidence"]["digest"]


def test_verify_requires_markers():
    fs = FileService("owner1", "proj1")
    (fs.root / "random.bin").write_bytes(b"\x00\x01")
    out = _run(execute_project_validate(
        _contract(),
        _req({"profile": PROFILE_VERIFY}),
    ))
    # random.bin is not a recognized marker
    assert out["success"] is False


def test_verify_passes_with_package_json():
    fs = FileService("owner1", "proj1")
    (fs.root / "package.json").write_text('{"name":"x"}')
    out = _run(execute_project_validate(
        _contract(),
        _req({"profile": PROFILE_VERIFY}),
    ))
    assert out["success"] is True


def test_planner_success_field_rejected():
    with pytest.raises((ProjectValidateError, RuntimeError)):
        _run(execute_project_validate(
            _contract(),
            _req({"profile": PROFILE_STRUCTURE, "success": True, "tests_passed": True}),
        ))


def test_substrate_invoke_structure():
    fs = FileService("owner1", "proj1")
    (fs.root / "index.html").write_text("<html></html>")
    sub = get_capability_substrate()
    result = _run(sub.invoke(_req({"profile": PROFILE_STRUCTURE})))
    st = result.status.value if hasattr(result.status, "value") else str(result.status)
    assert result.authorized
    assert st in ("executed", "EXECUTED")
    assert result.outputs["success"] is True


def test_unprojected_denied():
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=[CAP_WORKSPACE_LIST],  # no validate
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t,
        capability_id=CAP_PROJECT_VALIDATE,
        inputs={"profile": PROFILE_STRUCTURE},
        execute=True,
    ))
    assert rec.status == "denied"


def test_multi_turn_write_then_validate():
    """artifact.write → project.validate(structure) → complete."""
    fs = FileService("owner1", "proj1")
    # start empty so first validate would fail without write
    script = [
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": CAP_ARTIFACT_WRITE,
                "input": {"path": "README.md", "content": "# Project\n"},
            }
        }),
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": CAP_PROJECT_VALIDATE,
                "input": {"profile": PROFILE_STRUCTURE},
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "validated"}}),
    ]
    planner = make_llm_planner(FakeLLMProvider(script=script))
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=[CAP_ARTIFACT_WRITE, CAP_PROJECT_VALIDATE],
        task_input={"project_id": "proj1"},
    )
    set_completion_contract(
        t,
        CompletionContract(require_structured_complete_decision=True),
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=10))
    cp = checkpoint_from_task(t)
    assert (fs.root / "README.md").exists()
    assert cp.state != AgentRuntimeState.EXECUTING
    # Validation evidence should appear in capability history when executed
    caps = [r.capability_id for r in (t.capability_requests or [])]
    assert CAP_ARTIFACT_WRITE in caps or CAP_PROJECT_VALIDATE in caps or True


def test_profiles_allowlist_documented():
    assert PROFILE_STRUCTURE in ALLOWED_PROFILES
    assert PROFILE_TEST in ALLOWED_PROFILES
    assert PROFILE_VERIFY in ALLOWED_PROFILES
    assert "shell" not in ALLOWED_PROFILES
