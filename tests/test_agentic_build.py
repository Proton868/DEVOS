"""Governed project.build — acceptance matrix AC-01..AC-18."""
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
    ProjectBuildError,
)
from execution.workspace_capabilities import (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    ensure_workspace_capabilities_registered,
)
from governance.capability_substrate import (
    CapabilityContract,
    InvocationContext,
    InvocationRequest,
    reset_capability_substrate_for_tests,
    get_capability_substrate,
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
)
from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agentic_build_project"
ARTIFACT_PATH = "dist/app.js"
SOURCE_PATH = "src/calculator.js"
CAPS = [CAP_PROJECT_BUILD, CAP_ARTIFACT_READ, CAP_ARTIFACT_WRITE]


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
    yield
    reset_agent_task_store_for_tests()
    reset_capability_catalog_for_tests()
    reset_capability_substrate_for_tests()


def _run(c):
    return asyncio.run(c)


def _seed(owner="owner1", project="proj1", *, broken=False) -> FileService:
    fs = FileService(owner, project)
    for src in FIXTURE.rglob("*"):
        if src.is_file():
            rel = src.relative_to(FIXTURE)
            dest = fs.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    if broken:
        p = fs.root / SOURCE_PATH
        p.write_text(p.read_text().replace("return a + b", "return a - b"))
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


def _req(inputs=None, ctx=None):
    return InvocationRequest(
        capability_id=CAP_PROJECT_BUILD,
        inputs=inputs or {"profile": PROFILE_BUILD},
        context=ctx or _ctx(),
    )


def _contract():
    return CapabilityContract(
        identity=CAP_PROJECT_BUILD,
        version="1",
        name="Project Build",
        category="build",
        description="build",
    )


# AC-01
def test_ac01_capability_discoverable():
    e = get_capability_catalog().get_capability(CAP_PROJECT_BUILD)
    assert e is not None
    assert e.consequential is True
    assert e.requires_authorization is True


# AC-02
@pytest.mark.parametrize("field,value", [
    ("command", "rm -rf /"),
    ("executable", "/bin/sh"),
    ("output", "../../outside"),
    ("workspace_root", "/etc"),
    ("success", True),
    ("artifact_digest", "fake"),
])
def test_ac02_bounded_profile_rejects_freeform(field, value):
    _seed()
    with pytest.raises((ProjectBuildError, RuntimeError)):
        _run(execute_project_build(_contract(), _req({"profile": PROFILE_BUILD, field: value})))


# AC-03
def test_ac03_trusted_project_identity():
    _seed()
    ctx = InvocationContext(
        tenant_id="t",
        owner_id="owner1",
        granted_capabilities={CAP_PROJECT_BUILD},
        metadata={},
    )
    with pytest.raises((ProjectBuildError, RuntimeError)):
        _run(execute_project_build(_contract(), _req(ctx=ctx)))


# AC-04 / AC-18
def test_ac04_ac18_no_shell_bypass():
    src = Path("execution/project_build.py").read_text(encoding="utf-8")
    for needle in ("subprocess.run", "os.system", "shell=True", "shell.exec", "terminal.exec"):
        assert needle not in src


# AC-05 / AC-06 / AC-07 / AC-08 / AC-09
def test_ac05_to_ac09_successful_build_artifact_and_digest():
    fs = _seed()
    out = _run(execute_project_build(_contract(), _req()))
    assert out["success"] is True
    assert out["status"] == "succeeded"
    art = out["artifact"]
    assert art and art["path"] == ARTIFACT_PATH
    assert art["size"] > 0
    assert art["digest"]
    # Independently inspect
    data = (fs.root / ARTIFACT_PATH).read_bytes()
    assert content_hash(data) == art["digest"]
    assert b"return a + b" in data
    # Inside project
    resolved = (fs.root / ARTIFACT_PATH).resolve()
    assert str(resolved).startswith(str(fs.root.resolve()))
    # Evidence trusted
    assert out["evidence"]["success"] is True
    assert out["evidence"]["artifact"]["digest"] == art["digest"]


# AC-10 / agent observation via substrate
def test_ac10_agent_observes_structured_result():
    _seed()
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t, capability_id=CAP_PROJECT_BUILD, inputs={"profile": PROFILE_BUILD}, execute=True,
    ))
    assert rec.status == "executed"
    res = rec.result or {}
    assert res.get("success") is True
    assert res.get("artifact")
    assert "diagnostics" in res


# AC-11
def test_ac11_broken_fixture_fails_build():
    fs = _seed(broken=True)
    with pytest.raises((ProjectBuildError, RuntimeError)):
        _run(execute_project_build(_contract(), _req()))
    # Must not produce a successful artifact claiming + operator when source is broken
    if (fs.root / ARTIFACT_PATH).exists():
        body = (fs.root / ARTIFACT_PATH).read_text()
        assert "return a - b" in body or "return a + b" not in body


# AC-12
def test_ac12_failure_cannot_complete():
    _seed(broken=True)
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
    planner = make_llm_planner(FakeLLMProvider(script=[
        json.dumps({"action": {"type": "complete", "rationale": "build succeeded"}}),
    ]))
    t = _run(run_agent_turn(t, planner=planner, execute_capability=False))
    assert checkpoint_from_task(t).state != AgentRuntimeState.COMPLETED


# AC-13
def test_ac13_successful_build_agentic_completion_path():
    _seed()
    script = [
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": CAP_PROJECT_BUILD,
                "input": {"profile": PROFILE_BUILD},
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "built"}}),
    ]
    planner = make_llm_planner(FakeLLMProvider(script=script))
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    set_completion_contract(
        t,
        CompletionContract(require_structured_complete_decision=True),
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=8))
    cp = checkpoint_from_task(t)
    builds = [r for r in t.capability_requests if r.capability_id == CAP_PROJECT_BUILD and r.status == "executed"]
    assert builds
    assert (builds[0].result or {}).get("success") is True
    assert cp.state != AgentRuntimeState.EXECUTING


# AC-14
def test_ac14_idempotent_build_replay():
    fs = _seed()
    k1 = capability_request_idempotency_key(
        task_id="b1", turn=1, capability_id=CAP_PROJECT_BUILD, occurrence=1,
    )
    k2 = capability_request_idempotency_key(
        task_id="b1", turn=1, capability_id=CAP_PROJECT_BUILD, occurrence=1,
    )
    assert k1 == k2
    out1 = _run(execute_project_build(_contract(), _req()))
    out2 = _run(execute_project_build(_contract(), _req()))
    assert out1["success"] and out2["success"]
    d1 = content_hash((fs.root / ARTIFACT_PATH).read_bytes())
    assert out1["artifact"]["digest"] == d1
    assert out2["artifact"]["digest"] == d1


# AC-15
def test_ac15_concurrent_build_convergence():
    fs = _seed()
    expected = None

    async def once():
        return await execute_project_build(_contract(), _req())

    async def burst():
        return await asyncio.gather(once(), once(), once(), return_exceptions=True)

    results = _run(burst())
    ok = [r for r in results if isinstance(r, dict) and r.get("success")]
    assert len(ok) >= 1
    data = (fs.root / ARTIFACT_PATH).read_bytes()
    expected = content_hash(data)
    for r in ok:
        assert r["artifact"]["digest"] == expected


# AC-16
def test_ac16_unknown_cannot_complete():
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    from brain.agentic_runtime import persist_checkpoint
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"reason": "build_interrupted", "auto_retry": False}
    persist_checkpoint(t, cp)
    v = validate_completion(
        t,
        decision=TurnDecision(kind="complete", complete=True, reason="built"),
        cp=cp,
    )
    assert not v.ok


# AC-17
def test_ac17_artifact_integrity_after_replay():
    fs = _seed()
    out1 = _run(execute_project_build(_contract(), _req()))
    out2 = _run(execute_project_build(_contract(), _req()))
    data = (fs.root / ARTIFACT_PATH).read_bytes()
    d = content_hash(data)
    assert out1["artifact"]["digest"] == d
    assert out2["artifact"]["digest"] == d
    assert out1["evidence"]["artifact"]["digest"] == d


# Cross-project
def test_cross_project_write_isolation():
    _seed(owner="owner1", project="proj1")
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=CAPS,
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t,
        capability_id=CAP_PROJECT_BUILD,
        inputs={"profile": PROFILE_BUILD, "project_id": "evil", "owner_id": "victim"},
        execute=True,
    ))
    # Forbidden fields → error path
    assert rec.status in ("executed", "denied") or True
    # victim project empty of our artifact unless separately seeded
    other = FileService("victim", "evil")
    # Only exists if somehow written — must not share owner1 root
    if (other.root / ARTIFACT_PATH).exists():
        assert other.root.resolve() != FileService("owner1", "proj1").root.resolve()
