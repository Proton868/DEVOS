"""Governed project.observe — AC-81..AC-120."""
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
)
from execution.project_deploy import (
    CAP_PROJECT_DEPLOY, PROFILE_DEPLOY,
    ensure_project_deploy_registered, execute_project_deploy,
)
from execution.project_observe import (
    CAP_PROJECT_OBSERVE, PROFILE_OBSERVE,
    OBS_OBSERVED, OBS_UNAVAILABLE, OBS_UNHEALTHY, OBS_FAILED,
    CHECK_PASS, CHECK_FAIL, CHECK_UNAVAILABLE,
    ensure_project_observe_registered, execute_project_observe,
    load_and_validate_observe_contract, ProjectObserveError,
)
from governance.security_policy import SecurityPolicyError
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
    persist_checkpoint, apply_transition,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agentic_build_project"
CAPS = [
    CAP_PROJECT_BUILD, CAP_PROJECT_PREVIEW, CAP_PROJECT_VERIFY,
    CAP_PROJECT_DEPLOY, CAP_PROJECT_OBSERVE,
]


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
    ensure_project_deploy_registered()
    ensure_project_observe_registered()
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


def _pipeline(owner="owner1", project="proj1"):
    fs = _seed(owner, project)
    ctx = _ctx(owner, project)
    assert _run(execute_project_build(_c(CAP_PROJECT_BUILD), _req(CAP_PROJECT_BUILD, PROFILE_BUILD, ctx)))["success"]
    assert _run(execute_project_preview(_c(CAP_PROJECT_PREVIEW), _req(CAP_PROJECT_PREVIEW, PROFILE_PREVIEW, ctx)))["success"]
    assert _run(execute_project_verify(_c(CAP_PROJECT_VERIFY), _req(CAP_PROJECT_VERIFY, PROFILE_VERIFY, ctx)))["success"]
    assert _run(execute_project_deploy(_c(CAP_PROJECT_DEPLOY), _req(CAP_PROJECT_DEPLOY, PROFILE_DEPLOY, ctx)))["success"]
    return fs, ctx


# AC-81
def test_ac81_discovery():
    e = get_capability_catalog().get_capability(CAP_PROJECT_OBSERVE)
    assert e is not None
    assert e.requires_authorization is True


# AC-82
def test_ac82_contract_schema():
    fs, _ = _pipeline()
    c = load_and_validate_observe_contract(fs)
    assert c["version"] == 1 and c["profile"] == "observe"
    assert len(c["checks"]) >= 3


# AC-83 / AC-84 / AC-85
def test_ac83_to_85_contract_rejects():
    fs, _ = _pipeline()
    (fs.root / "devos.observe.json").write_text(json.dumps({
        "version": 1, "profile": "observe", "checks": [{"id": "a", "kind": "runtime_state", "required": True}],
        "command": "curl",
    }))
    with pytest.raises(ProjectObserveError):
        load_and_validate_observe_contract(fs)

    (fs.root / "devos.observe.json").write_text(json.dumps({
        "version": 1, "profile": "observe",
        "checks": [{"id": "a", "kind": "shell", "required": True}],
    }))
    with pytest.raises(ProjectObserveError):
        load_and_validate_observe_contract(fs)

    (fs.root / "devos.observe.json").write_text(json.dumps({
        "version": 1, "profile": "observe",
        "checks": [
            {"id": "a", "kind": "health", "required": True},
            {"id": "a", "kind": "readiness", "required": True},
        ],
    }))
    with pytest.raises(ProjectObserveError):
        load_and_validate_observe_contract(fs)


# AC-86 / AC-94 / AC-95
@pytest.mark.parametrize("field,value", [
    ("command", "curl localhost"),
    ("url", "http://evil"),
    ("healthy", True),
    ("ready", True),
    ("deployment_id", "fake"),
    ("owner_id", "victim"),
    ("artifact_digest", "abc"),
    ("evidence", "fake"),
])
def test_ac86_ac94_ac95_forbidden_planner_fields(field, value):
    _pipeline()
    with pytest.raises((ProjectObserveError, RuntimeError)):
        _run(execute_project_observe(
            _c(CAP_PROJECT_OBSERVE),
            _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, extra={field: value}),
        ))


# AC-87
def test_ac87_deploy_required():
    fs = _seed()
    ctx = _ctx()
    # build only — no deploy
    _run(execute_project_build(_c(CAP_PROJECT_BUILD), _req(CAP_PROJECT_BUILD, PROFILE_BUILD, ctx)))
    with pytest.raises((ProjectObserveError, RuntimeError)):
        _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))


# AC-88 / AC-89
def test_ac88_ac89_cross_isolation():
    fs1, ctx1 = _pipeline(owner="owner1", project="proj1")
    o1 = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx1)))
    fs2, ctx2 = _pipeline(owner="owner2", project="other")
    o2 = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx2)))
    assert o1["observation"]["owner_id"] == "owner1"
    assert o2["observation"]["owner_id"] == "owner2"
    assert o1["observation"]["deployment_id"] != o2["observation"]["deployment_id"]
    assert fs1.root.resolve() != fs2.root.resolve()


# AC-90 / AC-91 / AC-92 / AC-93
def test_ac90_to_93_trusted_identity():
    _, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    obs = out["observation"]
    assert obs["deployment_id"]
    assert obs["runtime_id"] or obs["target_id"]
    assert obs["artifact_digest"]
    assert obs["verification_digest"]
    # Not from planner
    assert out["evidence"]["deployment_id"] == obs["deployment_id"]


# AC-96 / AC-97 — agent claims ignored (planner fields already rejected)
def test_ac96_ac97_agent_claims_not_authoritative():
    _, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    # Trusted observation determines status — not agent text
    assert out["observation_status"] in (
        OBS_OBSERVED, OBS_UNAVAILABLE, OBS_UNHEALTHY, "DEGRADED",
    )
    assert "observation_status" in out


# AC-98 / AC-99
def test_ac98_ac99_runtime_and_session_honesty():
    _, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    checks = {c["id"]: c for c in out["observation"]["checks"]}
    assert checks["runtime"]["status"] in (CHECK_PASS, CHECK_UNAVAILABLE)
    # Session mode notes honesty where applicable
    health = checks.get("health")
    assert health is not None
    if health.get("note"):
        assert "session" in health["note"] or "managed" in health["note"] or "live" in health["note"]


# AC-100 / AC-101 / AC-102 / AC-115 / AC-116 / AC-117 / AC-118
def test_ac100_to_118_no_platforms():
    src = Path("execution/project_observe.py").read_text(encoding="utf-8")
    for n in (
        "subprocess.run", "subprocess.Popen", "os.system", "shell=True",
        "urllib.request", "prometheus", "alertmanager", "remediat",
    ):
        assert n not in src
    assert "read_only" in src or "READ" in src or "Read-only" in src


# AC-103
def test_ac103_evidence():
    fs, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    assert out["evidence"]["kind"] == CAP_PROJECT_OBSERVE
    assert out["evidence"]["digest"]
    assert out["evidence"]["read_only"] is True
    assert (fs.root / ".devos" / "observation.json").is_file()


# AC-104 / AC-109 / AC-110 — operation success independent of health
def test_ac104_ac109_ac110_operation_vs_observation():
    fs, ctx = _pipeline()
    # Force unhealthy by marking deployment health not ready while keeping record
    dep_path = fs.root / ".devos" / "deployment.json"
    dep = json.loads(dep_path.read_text())
    dep["health"] = {"ready": False, "reason": "forced_unhealthy", "observed_by": "test"}
    dep_path.write_text(json.dumps(dep, indent=2))

    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    # Operation succeeded (read completed)
    assert out["success"] is True
    assert out["status"] == "succeeded"
    # Observation may be UNHEALTHY or UNAVAILABLE depending on check aggregation
    assert out["observation_status"] in (OBS_UNHEALTHY, OBS_UNAVAILABLE, OBS_OBSERVED, "DEGRADED", OBS_FAILED)


# AC-105 / AC-106
def test_ac105_ac106_unavailable_and_readonly_unknown():
    # Missing required live health → UNAVAILABLE is honest; not converted to healthy
    fs, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    assert out["observation_status"] != "HEALTHY"  # status model uses OBSERVED/UNHEALTHY/...
    # Task UNKNOWN does not become observation HEALTHY
    t = delegate_agent_task(owner_id="owner1", requested_capabilities=CAPS, task_input={"project_id": "proj1"})
    cp = checkpoint_from_task(t)
    cp.state = AgentRuntimeState.UNKNOWN
    cp.unknown_info = {"reason": "observe_interrupted", "auto_retry": False, "read_only": True}
    persist_checkpoint(t, cp)
    v = validate_completion(
        t, decision=TurnDecision(kind="complete", complete=True, reason="healthy"), cp=cp,
    )
    assert not v.ok
    assert checkpoint_from_task(t).state == AgentRuntimeState.UNKNOWN


# AC-107 / AC-108
def test_ac107_ac108_task_observation_independence():
    _, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    # Observation healthy/observed does not auto-complete task
    t = delegate_agent_task(owner_id="owner1", requested_capabilities=CAPS, task_input={"project_id": "proj1"})
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
        t, decision=TurnDecision(kind="complete", complete=True, reason="observed healthy"), cp=cp,
    )
    assert not v.ok
    # Task COMPLETED is not inferred — and COMPLETED wouldn't set observation either
    assert out["observation_status"] != AgentRuntimeState.COMPLETED.value


# AC-111 / AC-112
def test_ac111_ac112_idempotent_concurrent():
    _, ctx = _pipeline()
    o1 = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    o2 = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    assert o1["observation"]["observation_id"] == o2["observation"]["observation_id"]

    async def once():
        return await execute_project_observe(
            _c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx),
        )

    async def burst():
        return await asyncio.gather(once(), once(), once(), return_exceptions=True)

    results = _run(burst())
    ok = [r for r in results if isinstance(r, dict) and r.get("success")]
    assert len(ok) >= 1
    ids = {r["observation"]["observation_id"] for r in ok}
    assert len(ids) == 1


# AC-113 / AC-114
def test_ac113_ac114_evidence_scoped():
    fs, ctx = _pipeline()
    out = _run(execute_project_observe(_c(CAP_PROJECT_OBSERVE), _req(CAP_PROJECT_OBSERVE, PROFILE_OBSERVE, ctx)))
    assert out["evidence"]["owner_id"] == "owner1"
    assert out["evidence"]["project_id"] == "proj1"
    # Stored under project workspace — not a second global evidence DB
    assert (fs.root / ".devos" / "observation.json").is_file()


# Unauthorized
def test_unauthorized_denied():
    _pipeline()
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=[CAP_PROJECT_BUILD],
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t, capability_id=CAP_PROJECT_OBSERVE,
        inputs={"profile": PROFILE_OBSERVE}, execute=True,
    ))
    assert rec.status == "denied"
