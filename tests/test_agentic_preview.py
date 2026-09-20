"""Governed project.preview — acceptance matrix AC-01..AC-24."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from execution.artifacts import content_hash
from execution.files import FileService
from execution.project_build import (
    CAP_PROJECT_BUILD,
    PROFILE_BUILD,
    ensure_project_build_registered,
    execute_project_build,
)
from execution.project_preview import (
    CAP_PROJECT_PREVIEW,
    PROFILE_PREVIEW,
    STATE_READY,
    STATE_STOPPED,
    STATE_UNKNOWN,
    ensure_project_preview_registered,
    execute_project_preview,
    stop_preview_session,
    ProjectPreviewError,
    SESSION_REL,
)
from execution.workspace_capabilities import ensure_workspace_capabilities_registered
from governance.capability_substrate import (
    CapabilityContract,
    InvocationContext,
    InvocationRequest,
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
    TurnDecision,
    checkpoint_from_task,
    run_agent_turn,
    run_agent_until_terminal,
    set_completion_contract,
    validate_completion,
    capability_request_idempotency_key,
    persist_checkpoint,
)
from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agentic_build_project"
CAPS = [CAP_PROJECT_BUILD, CAP_PROJECT_PREVIEW]


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
        tenant_id="ten1",
        owner_id=owner,
        granted_capabilities=set(grants or CAPS),
        actor_type="agent",
        actor_id=owner,
        surface="test",
        client_supplied_grants=False,
        metadata={"project_id": project},
    )


def _build_req(ctx=None):
    return InvocationRequest(
        capability_id=CAP_PROJECT_BUILD,
        inputs={"profile": PROFILE_BUILD},
        context=ctx or _ctx(),
    )


def _preview_req(inputs=None, ctx=None):
    return InvocationRequest(
        capability_id=CAP_PROJECT_PREVIEW,
        inputs=inputs or {"profile": PROFILE_PREVIEW},
        context=ctx or _ctx(),
    )


def _bcontract():
    return CapabilityContract(
        identity=CAP_PROJECT_BUILD, version="1", name="build",
        category="build", description="b",
    )


def _pcontract():
    return CapabilityContract(
        identity=CAP_PROJECT_PREVIEW, version="1", name="preview",
        category="preview", description="p",
    )


def _build_then(fs=None, owner="owner1", project="proj1"):
    if fs is None:
        fs = _seed(owner=owner, project=project)
    out = _run(execute_project_build(
        _bcontract(),
        _build_req(ctx=_ctx(owner, project)),
    ))
    assert out["success"] is True
    return fs, out


# AC-01
def test_ac01_discovery():
    e = get_capability_catalog().get_capability(CAP_PROJECT_PREVIEW)
    assert e is not None
    assert e.consequential is True


# AC-02 / AC-19 / AC-20
@pytest.mark.parametrize("field,value", [
    ("command", "node server.js"),
    ("executable", "/bin/sh"),
    ("port", 8080),
    ("ready", True),
    ("workspace_root", "/tmp"),
    ("owner_id", "victim"),
    ("url", "http://evil"),
])
def test_ac02_ac19_ac20_forbidden_fields(field, value):
    _build_then()
    with pytest.raises((ProjectPreviewError, RuntimeError)):
        _run(execute_project_preview(
            _pcontract(),
            _preview_req({"profile": PROFILE_PREVIEW, field: value}),
        ))


# AC-03
def test_ac03_trusted_identity():
    _seed()
    ctx = InvocationContext(
        tenant_id="t", owner_id="owner1",
        granted_capabilities={CAP_PROJECT_PREVIEW}, metadata={},
    )
    with pytest.raises((ProjectPreviewError, RuntimeError)):
        _run(execute_project_preview(_pcontract(), _preview_req(ctx=ctx)))


# AC-04
def test_ac04_unauthorized_denied():
    _build_then()
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=[CAP_PROJECT_BUILD],  # no preview
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t, capability_id=CAP_PROJECT_PREVIEW,
        inputs={"profile": PROFILE_PREVIEW}, execute=True,
    ))
    assert rec.status == "denied"


# AC-05
def test_ac05_build_prerequisite_required():
    _seed()  # no build
    with pytest.raises((ProjectPreviewError, RuntimeError)):
        _run(execute_project_preview(_pcontract(), _preview_req()))


# AC-06 / AC-24
def test_ac06_ac24_no_host_bypass_no_second_engine():
    src = Path("execution/project_preview.py").read_text(encoding="utf-8")
    for needle in ("subprocess.Popen", "subprocess.run", "os.system", "shell=True"):
        assert needle not in src
    assert "runtime_service" in src or "session" in src


# AC-07 / AC-08 / AC-09 / AC-10
def test_ac07_to_ac10_runtime_identity_readiness_observation():
    fs, _ = _build_then()
    out = _run(execute_project_preview(_pcontract(), _preview_req()))
    assert out["success"] is True
    rt = out["runtime"]
    assert rt["runtime_id"]
    assert rt["owner_id"] == "owner1"
    assert rt["project_id"] == "proj1"
    assert rt["state"] == STATE_READY
    assert rt["readiness"]["ready"] is True
    assert rt["readiness"]["observed_by"] == "project_preview"
    # Durable session file
    assert (fs.root / ".devos" / "preview_session.json").is_file()
    # Evidence
    assert out["evidence"]["runtime_id"] == rt["runtime_id"]
    assert out["evidence"]["build_digest"]
    assert out["evidence"]["digest"]


# AC-11 / AC-12
def test_ac11_ac12_failure_cannot_complete():
    """Preview without build fails; completion without successful capability is rejected."""
    _seed()
    # Direct capability fails without build artifact
    with pytest.raises((ProjectPreviewError, RuntimeError)):
        _run(execute_project_preview(_pcontract(), _preview_req()))

    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=CAPS,
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
    from brain.agentic_runtime import apply_transition
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)
    v = validate_completion(
        t,
        decision=TurnDecision(kind="complete", complete=True, reason="preview started"),
        cp=cp,
    )
    assert not v.ok


# AC-13
def test_ac13_successful_preview_agentic_path():
    _seed()
    script = [
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": CAP_PROJECT_BUILD,
                "input": {"profile": PROFILE_BUILD},
            }
        }),
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": CAP_PROJECT_PREVIEW,
                "input": {"profile": PROFILE_PREVIEW},
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "preview ready"}}),
    ]
    planner = make_llm_planner(FakeLLMProvider(script=script))
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    set_completion_contract(
        t, CompletionContract(require_structured_complete_decision=True),
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=10))
    previews = [
        r for r in t.capability_requests
        if r.capability_id == CAP_PROJECT_PREVIEW and r.status == "executed"
    ]
    assert previews
    assert (previews[0].result or {}).get("success") is True
    assert checkpoint_from_task(t).state != AgentRuntimeState.EXECUTING


# AC-14
def test_ac14_idempotent_replay():
    fs, _ = _build_then()
    out1 = _run(execute_project_preview(_pcontract(), _preview_req()))
    out2 = _run(execute_project_preview(_pcontract(), _preview_req()))
    assert out1["success"] and out2["success"]
    assert out1["runtime"]["runtime_id"] == out2["runtime"]["runtime_id"]
    assert out2["diagnostics"].get("idempotent") is True
    k1 = capability_request_idempotency_key(
        task_id="p1", turn=1, capability_id=CAP_PROJECT_PREVIEW, occurrence=1,
    )
    k2 = capability_request_idempotency_key(
        task_id="p1", turn=1, capability_id=CAP_PROJECT_PREVIEW, occurrence=1,
    )
    assert k1 == k2


# AC-15
def test_ac15_concurrent_convergence():
    _build_then()

    async def once():
        return await execute_project_preview(_pcontract(), _preview_req())

    async def burst():
        return await asyncio.gather(once(), once(), once(), return_exceptions=True)

    results = _run(burst())
    ok = [r for r in results if isinstance(r, dict) and r.get("success")]
    assert len(ok) >= 1
    ids = {r["runtime"]["runtime_id"] for r in ok}
    assert len(ids) == 1  # same logical session


# AC-16
def test_ac16_unknown_cannot_complete():
    t = delegate_agent_task(
        owner_id="owner1", requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"reason": "preview_interrupted", "auto_retry": False}
    persist_checkpoint(t, cp)
    v = validate_completion(
        t, decision=TurnDecision(kind="complete", complete=True, reason="ready"), cp=cp,
    )
    assert not v.ok


# AC-17 / AC-18
def test_ac17_ac18_cross_owner_project_isolation():
    fs1, _ = _build_then(owner="owner1", project="proj1")
    out1 = _run(execute_project_preview(_pcontract(), _preview_req(ctx=_ctx("owner1", "proj1"))))
    rid1 = out1["runtime"]["runtime_id"]

    # owner2 cannot use owner1 session via their context
    fs2 = _seed(owner="owner2", project="other")
    # Build for owner2 then preview
    _run(execute_project_build(_bcontract(), _build_req(ctx=_ctx("owner2", "other"))))
    out2 = _run(execute_project_preview(_pcontract(), _preview_req(ctx=_ctx("owner2", "other"))))
    assert out2["runtime"]["owner_id"] == "owner2"
    assert out2["runtime"]["project_id"] == "other"
    assert out2["runtime"]["runtime_id"] != rid1
    # Sessions stored under different roots
    assert fs1.root.resolve() != fs2.root.resolve()


# AC-21
def test_ac21_stop_is_project_scoped():
    fs, _ = _build_then()
    out = _run(execute_project_preview(_pcontract(), _preview_req()))
    assert out["runtime"]["state"] == STATE_READY
    stopped = stop_preview_session("owner1", "proj1")
    assert stopped["runtime"]["state"] == STATE_STOPPED
    # Re-read session
    data = json.loads((fs.root / ".devos" / "preview_session.json").read_text())
    assert data["state"] == STATE_STOPPED
    assert data["readiness"]["ready"] is False


# AC-22
def test_ac22_stale_session_reconcile_on_rebuild():
    fs, build1 = _build_then()
    out1 = _run(execute_project_preview(_pcontract(), _preview_req()))
    rid1 = out1["runtime"]["runtime_id"]
    # Mutate build artifact → new digest; next preview creates new logical session
    art = fs.root / "dist" / "app.js"
    art.write_bytes(art.read_bytes() + b"\n// touch\n")
    # Update is still a valid build artifact file; preview binds to new digest
    out2 = _run(execute_project_preview(_pcontract(), _preview_req()))
    assert out2["runtime"]["build_digest"] != out1["runtime"]["build_digest"]
    assert out2["runtime"]["runtime_id"] != rid1


# AC-23
def test_ac23_evidence_not_planner_forged():
    _build_then()
    with pytest.raises((ProjectPreviewError, RuntimeError)):
        _run(execute_project_preview(
            _pcontract(),
            _preview_req({
                "profile": PROFILE_PREVIEW,
                "ready": True,
                "evidence": "fake",
                "success": True,
            }),
        ))
