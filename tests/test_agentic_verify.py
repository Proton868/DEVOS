"""Governed project.verify — AC-25..AC-48 (READY ≠ VERIFIED)."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from execution.files import FileService
from execution.project_build import (
    CAP_PROJECT_BUILD, PROFILE_BUILD,
    ensure_project_build_registered, execute_project_build,
)
from execution.project_preview import (
    CAP_PROJECT_PREVIEW, PROFILE_PREVIEW,
    ensure_project_preview_registered, execute_project_preview,
)
from execution.project_verify import (
    CAP_PROJECT_VERIFY, PROFILE_VERIFY,
    ensure_project_verify_registered, execute_project_verify,
    ProjectVerifyError,
)
from execution.workspace_capabilities import ensure_workspace_capabilities_registered
from governance.capability_substrate import (
    CapabilityContract, InvocationContext, InvocationRequest,
    reset_capability_substrate_for_tests,
)
from governance.capability_catalog import (
    get_capability_catalog, reset_capability_catalog_for_tests,
)
from brain.agentic_automation import (
    delegate_agent_task, request_capability, reset_agent_task_store_for_tests,
)
from brain.agentic_runtime import (
    AgentRuntimeState, CompletionContract, TurnDecision,
    checkpoint_from_task, set_completion_contract, validate_completion,
    capability_request_idempotency_key, persist_checkpoint, apply_transition,
    run_agent_until_terminal,
)
from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agentic_build_project"
CAPS = [CAP_PROJECT_BUILD, CAP_PROJECT_PREVIEW, CAP_PROJECT_VERIFY]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr("execution.files.PROJECTS_DIR", root)
    reset_capability_substrate_for_tests()
    reset_capability_catalog_for_tests()
    reset_agent_task_store_for_tests()
    ensure_workspace_capabilities_registered()
    ensure_project_build_registered()
    ensure_project_preview_registered()
    ensure_project_verify_registered()
    yield
    reset_agent_task_store_for_tests()
    reset_capability_catalog_for_tests()
    reset_capability_substrate_for_tests()


def _run(c):
    return asyncio.run(c)


def _seed(owner="owner1", project="proj1") -> FileService:
    fs = FileService(owner, project)
    for src in FIXTURE.rglob("*"):
        if src.is_file():
            rel = src.relative_to(FIXTURE)
            dest = fs.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return fs


def _ctx(owner="owner1", project="proj1", grants=None):
    return InvocationContext(
        tenant_id="ten1", owner_id=owner,
        granted_capabilities=set(grants or CAPS),
        actor_type="agent", actor_id=owner, surface="test",
        client_supplied_grants=False, metadata={"project_id": project},
    )


def _req(cap, profile, ctx=None, extra=None):
    inputs = {"profile": profile}
    if extra:
        inputs.update(extra)
    return InvocationRequest(capability_id=cap, inputs=inputs, context=ctx or _ctx())


def _c(cap, cat="x"):
    return CapabilityContract(identity=cap, version="1", name=cap, category=cat, description="d")


def _pipeline(owner="owner1", project="proj1", *, break_app=False):
    fs = _seed(owner, project)
    ctx = _ctx(owner, project)
    b = _run(execute_project_build(_c(CAP_PROJECT_BUILD, "build"), _req(CAP_PROJECT_BUILD, PROFILE_BUILD, ctx)))
    assert b["success"]
    if break_app:
        art = fs.root / "dist" / "app.js"
        art.write_text(art.read_text().replace("return a + b", "return a - b"))
        # Keep session pointing at old digest will fail build_digest_match;
        # also must_contain/forbid will fail. Re-preview to rebind session:
        # For READY≠VERIFIED we want session READY but app wrong — so leave session
        # and mutate artifact only after preview.
    p = _run(execute_project_preview(_c(CAP_PROJECT_PREVIEW, "preview"), _req(CAP_PROJECT_PREVIEW, PROFILE_PREVIEW, ctx)))
    assert p["success"]
    if break_app:
        art = fs.root / "dist" / "app.js"
        text = art.read_text().replace("return a + b", "return a - b")
        art.write_text(text)
    return fs, ctx


# AC-25
def test_ac25_discovery():
    e = get_capability_catalog().get_capability(CAP_PROJECT_VERIFY)
    assert e is not None and e.consequential is True


# AC-26 / AC-32 / AC-46
@pytest.mark.parametrize("field,value", [
    ("command", "curl localhost"),
    ("executable", "/bin/sh"),
    ("verified", True),
    ("ready", True),
    ("port", 3000),
    ("url", "http://evil"),
    ("runtime_id", "fake"),
    ("owner_id", "victim"),
])
def test_ac26_ac32_ac46_forbidden(field, value):
    _pipeline()
    with pytest.raises((ProjectVerifyError, RuntimeError)):
        _run(execute_project_verify(
            _c(CAP_PROJECT_VERIFY, "verify"),
            _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, extra={field: value}),
        ))


# AC-27
def test_ac27_trusted_identity():
    _seed()
    ctx = InvocationContext(
        tenant_id="t", owner_id="owner1",
        granted_capabilities={CAP_PROJECT_VERIFY}, metadata={},
    )
    with pytest.raises((ProjectVerifyError, RuntimeError)):
        _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))


# AC-28
def test_ac28_unauthorized():
    _pipeline()
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=[CAP_PROJECT_BUILD],
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t, capability_id=CAP_PROJECT_VERIFY,
        inputs={"profile": PROFILE_VERIFY}, execute=True,
    ))
    assert rec.status == "denied"


# AC-29
def test_ac29_build_required():
    _seed()
    with pytest.raises((ProjectVerifyError, RuntimeError)):
        _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY)))


# AC-30
def test_ac30_preview_required():
    fs = _seed()
    ctx = _ctx()
    _run(execute_project_build(_c(CAP_PROJECT_BUILD), _req(CAP_PROJECT_BUILD, PROFILE_BUILD, ctx)))
    with pytest.raises((ProjectVerifyError, RuntimeError)):
        _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))


# AC-31 / AC-48
def test_ac31_ac48_no_bypass_no_second_engine():
    src = Path("execution/project_verify.py").read_text(encoding="utf-8")
    for n in ("subprocess.run", "subprocess.Popen", "os.system", "shell=True"):
        assert n not in src


# AC-33 / AC-34 / AC-35
def test_ac33_ready_not_verified_and_app_checks():
    fs, ctx = _pipeline()
    # READY session exists
    out = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    assert out["success"] is True
    assert out["status"] == "verified"
    assert out["diagnostics"]["ready_not_verified"] is True
    names = {c["name"] for c in out["checks"]}
    assert "preview_session_ready" in names
    assert "must_contain" in names

    # Corrupt application after READY preview
    fs2, ctx2 = _pipeline(break_app=True)
    out2 = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx2)))
    assert out2["success"] is False
    assert out2["status"] == "failed"
    # Session was READY but verify failed → READY ≠ VERIFIED
    assert any(c["status"] == "failed" for c in out2["checks"])


# AC-36 / AC-37
def test_ac36_ac37_completion_gate():
    # Failure cannot complete
    t = delegate_agent_task(
        owner_id="owner1", requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    set_completion_contract(
        t,
        CompletionContract(
            require_structured_complete_decision=True,
            required_evidence=True,
            required_successful_capabilities=1,
        ),
    )
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    v = validate_completion(
        t, decision=TurnDecision(kind="complete", complete=True, reason="verified"), cp=cp,
    )
    assert not v.ok

    # Success path through agentic pipeline
    _seed()
    script = [
        json.dumps({"action": {"type": "capability_request", "capability": CAP_PROJECT_BUILD, "input": {"profile": PROFILE_BUILD}}}),
        json.dumps({"action": {"type": "capability_request", "capability": CAP_PROJECT_PREVIEW, "input": {"profile": PROFILE_PREVIEW}}}),
        json.dumps({"action": {"type": "capability_request", "capability": CAP_PROJECT_VERIFY, "input": {"profile": PROFILE_VERIFY}}}),
        json.dumps({"action": {"type": "complete", "rationale": "verified"}}),
    ]
    planner = make_llm_planner(FakeLLMProvider(script=script))
    t2 = delegate_agent_task(
        owner_id="owner1", requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    set_completion_contract(t2, CompletionContract(require_structured_complete_decision=True))
    t2 = _run(run_agent_until_terminal(t2, planner=planner, max_loops=12))
    verifies = [r for r in t2.capability_requests if r.capability_id == CAP_PROJECT_VERIFY and r.status == "executed"]
    assert verifies and (verifies[0].result or {}).get("success") is True


# AC-38
def test_ac38_evidence_trusted():
    _, ctx = _pipeline()
    out = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    assert out["evidence"]["kind"] == CAP_PROJECT_VERIFY
    assert out["evidence"]["digest"]
    assert out["evidence"]["ready_not_verified"] is True


# AC-39
def test_ac39_idempotent():
    _, ctx = _pipeline()
    o1 = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    o2 = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    assert o1["success"] and o2["success"]
    assert o1["status"] == o2["status"] == "verified"
    k1 = capability_request_idempotency_key(task_id="v1", turn=1, capability_id=CAP_PROJECT_VERIFY, occurrence=1)
    k2 = capability_request_idempotency_key(task_id="v1", turn=1, capability_id=CAP_PROJECT_VERIFY, occurrence=1)
    assert k1 == k2


# AC-40
def test_ac40_concurrent():
    _, ctx = _pipeline()

    async def once():
        return await execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx))

    async def burst():
        return await asyncio.gather(once(), once(), once(), return_exceptions=True)

    results = _run(burst())
    ok = [r for r in results if isinstance(r, dict) and r.get("success")]
    assert len(ok) >= 1
    assert all(r["status"] == "verified" for r in ok)


# AC-41
def test_ac41_unknown_cannot_complete():
    t = delegate_agent_task(owner_id="owner1", requested_capabilities=CAPS, task_input={"project_id": "proj1"})
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"reason": "verify_interrupted", "auto_retry": False}
    persist_checkpoint(t, cp)
    v = validate_completion(
        t, decision=TurnDecision(kind="complete", complete=True, reason="verified"), cp=cp,
    )
    assert not v.ok


# AC-42 / AC-43
def test_ac42_ac43_isolation():
    fs1, ctx1 = _pipeline(owner="owner1", project="proj1")
    out1 = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx1)))
    fs2, ctx2 = _pipeline(owner="owner2", project="other")
    out2 = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx2)))
    assert out1["runtime_id"] != out2["runtime_id"]
    assert fs1.root.resolve() != fs2.root.resolve()


# AC-44
def test_ac44_session_mode_honesty():
    _, ctx = _pipeline()
    out = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    assert out["preview"]["mode"] == "session"
    assert out["diagnostics"]["mode"] == "session"
    mode_check = next(c for c in out["checks"] if c["name"] == "mode_honesty")
    assert "session_mode" in (mode_check.get("detail") or "")


# AC-45 — runtime mode unavailable without isolation: fail closed if required
def test_ac45_runtime_mode_requires_runtime_session():
    fs, ctx = _pipeline()
    # Force contract to require runtime mode
    (fs.root / "devos.verify.json").write_text(json.dumps({
        "profile": "verify", "mode": "runtime",
        "must_contain": "return a + b",
    }))
    out = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    # Session mode cannot satisfy runtime-mode contract
    assert out["success"] is False


# AC-47
def test_ac47_durable_result():
    fs, ctx = _pipeline()
    out = _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))
    path = fs.root / ".devos" / "verify_result.json"
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["status"] == "verified"
    assert data["evidence_digest"] == out["evidence_digest"]
