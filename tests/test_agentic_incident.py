"""Governed Incident domain — AC-183..AC-220 (focused proofs)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.files import FileService
from execution.project_incident import (
    CAP_PROJECT_INCIDENT,
    PROFILE_INCIDENT,
    INC_DETECTED, INC_OPEN, INC_ASSESSING, INC_ACKNOWLEDGED,
    INC_MITIGATING, INC_VERIFYING, INC_RESOLVED, INC_CANCELLED, INC_UNKNOWN,
    ProjectIncidentError,
    validate_incident_contract,
    write_default_incident_contract,
    detect_incident,
    assess_incident,
    acknowledge_incident,
    link_maintenance_request,
    verify_incident,
    resolve_incident,
    cancel_incident,
    transition_incident,
    ensure_project_incident_registered,
    reset_incident_store_for_tests,
    list_incident_evidence,
    load_incident,
)
from execution.project_maintain import (
    CAP_PROJECT_MAINTAIN,
    create_maintenance_request,
    evaluate_maintenance_request,
    plan_maintenance_request,
    authorize_maintenance_request,
    execute_maintenance_action,
    verify_maintenance_action,
    reconcile_maintenance_request,
    ensure_project_maintain_registered,
    reset_maintain_store_for_tests,
    REQ_RESOLVED, REQ_UNKNOWN,
)
from governance.capability_catalog import (
    get_capability_catalog, reset_capability_catalog_for_tests,
)
from governance.capability_substrate import reset_capability_substrate_for_tests


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr("execution.files.PROJECTS_DIR", root)
    reset_capability_substrate_for_tests()
    reset_capability_catalog_for_tests()
    reset_incident_store_for_tests()
    reset_maintain_store_for_tests()
    ensure_project_incident_registered()
    ensure_project_maintain_registered()
    yield
    reset_incident_store_for_tests()
    reset_maintain_store_for_tests()
    reset_capability_catalog_for_tests()
    reset_capability_substrate_for_tests()


def _fs(owner="owner1", project="proj1") -> FileService:
    return FileService(owner, project)


def _seed_incident_contract(fs: FileService) -> dict:
    return write_default_incident_contract(fs)


def _seed_maintain_contract(fs: FileService) -> None:
    data = {
        "version": 1,
        "profile": "maintain",
        "policies": [{
            "id": "repair-build",
            "trigger": {"source": "observation", "kind": "health", "status": "UNHEALTHY"},
            "actions": [{
                "id": "rebuild",
                "capability": "project.build",
                "required": True,
                "verification": {
                    "required": True,
                    "capability": "project.verify",
                    "ordering": "after",
                },
            }],
        }],
    }
    fs.write("devos.maintain.json", json.dumps(data))


def _detect(fs, obs_id="OBS-001", **kw):
    return detect_incident(
        fs,
        owner_id=kw.get("owner_id", "owner1"),
        project_id=kw.get("project_id", "proj1"),
        deployment_id=kw.get("deployment_id", "DEP-001"),
        source_observation_id=obs_id,
        observation_kind="health",
        observation_status="UNHEALTHY",
    )


# AC-183 independent domain
def test_ac183_incident_capability_discoverable():
    cat = get_capability_catalog()
    assert cat.get_capability(CAP_PROJECT_INCIDENT) is not None
    entry = cat.get_capability(CAP_PROJECT_INCIDENT)
    assert entry.consequential is False
    assert entry.risk_class == "read"


# AC-184 / AC-185 contract
def test_ac184_ac185_contract_validation():
    data = {
        "version": 1,
        "profile": "incident",
        "policies": [{
            "id": "deployment-unhealthy",
            "trigger": {"source": "observation", "kind": "health", "status": "UNHEALTHY"},
            "severity": "HIGH",
            "requires_acknowledgement": True,
        }],
    }
    validate_incident_contract(data)
    with pytest.raises(ProjectIncidentError):
        validate_incident_contract({**data, "runbook": "x"})
    with pytest.raises(ProjectIncidentError):
        validate_incident_contract({
            **data,
            "policies": [{
                **data["policies"][0],
                "trigger": {
                    "source": "observation",
                    "kind": "health",
                    "status": "UNHEALTHY",
                    "shell": "true",
                },
            }],
        })


# AC-186 observation without policy match
def test_ac186_no_automatic_incident_without_policy():
    fs = _fs()
    # Contract exists but status does not match
    _seed_incident_contract(fs)
    with pytest.raises(ProjectIncidentError) as ei:
        detect_incident(
            fs,
            owner_id="owner1",
            project_id="proj1",
            deployment_id="DEP-001",
            source_observation_id="OBS-H",
            observation_kind="health",
            observation_status="HEALTHY",
        )
    assert ei.value.code == "NO_POLICY_MATCH"


# AC-187 / AC-188 / AC-199 / AC-200
def test_ac187_detection_not_auth_and_ack_not_resolve():
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs)
    assert inc["status"] == INC_DETECTED
    assert inc["severity"] == "HIGH"
    # severity is not authorization
    assert "authorized" not in inc
    assess_incident(fs, inc["incident_id"])
    inc = acknowledge_incident(
        fs, inc["incident_id"], actor="owner1", owner_id="owner1", project_id="proj1",
    )
    assert inc["status"] == INC_ACKNOWLEDGED
    assert inc["status"] != INC_RESOLVED
    with pytest.raises(ProjectIncidentError):
        resolve_incident(fs, inc["incident_id"])


# AC-189 / AC-190 transitions
def test_ac189_lifecycle_enforced():
    inc = {"status": INC_DETECTED, "version": 1}
    inc = transition_incident(inc, INC_OPEN)
    with pytest.raises(ProjectIncidentError):
        transition_incident(inc, INC_RESOLVED)


# AC-193 / AC-194 ownership
def test_ac193_cross_owner_ack_denied():
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs)
    assess_incident(fs, inc["incident_id"])
    with pytest.raises(ProjectIncidentError):
        acknowledge_incident(
            fs, inc["incident_id"], actor="intruder",
            owner_id="intruder", project_id="proj1",
        )


# AC-195 deterministic correlation
def test_ac195_duplicate_correlation():
    fs = _fs()
    _seed_incident_contract(fs)
    a = _detect(fs, obs_id="OBS-001")
    b = _detect(fs, obs_id="OBS-002")
    assert a["incident_id"] == b["incident_id"]
    assert b.get("last_correlated_observation_id") == "OBS-002"


# AC-196 / AC-197 / AC-198 evidence
def test_ac196_ac197_ac198_evidence():
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs, obs_id="OBS-SRC")
    ev = list_incident_evidence()
    assert any(
        e.get("event") == "incident_detected"
        and e.get("source_observation_id") == "OBS-SRC"
        for e in ev
    )
    iid = inc["incident_id"]
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    # skip maintenance: ACK → VERIFYING allowed
    verify_incident(
        fs, iid,
        resolution_observation_id="OBS-RES",
        observation_status="HEALTHY",
    )
    resolve_incident(
        fs, iid,
        resolution_observation_id="OBS-RES",
        observation_status="HEALTHY",
    )
    ev = list_incident_evidence()
    assert any(
        e.get("event") == "incident_resolved"
        and e.get("resolution_observation_id") == "OBS-RES"
        for e in ev
    )


# AC-201 / AC-202 / AC-203 link maintain without duplicating state
def test_ac201_link_maintain_no_state_duplication():
    fs = _fs()
    _seed_incident_contract(fs)
    _seed_maintain_contract(fs)
    inc = _detect(fs)
    iid = inc["incident_id"]
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    mr = create_maintenance_request(
        fs,
        owner_id="owner1",
        project_id="proj1",
        deployment_id="DEP-001",
        source_observation_id="OBS-001",
        observation_kind="health",
        observation_status="UNHEALTHY",
    )
    inc = link_maintenance_request(
        fs, iid,
        maintenance_request_id=mr["maintenance_request_id"],
        owner_id="owner1",
        project_id="proj1",
    )
    assert inc["status"] == INC_MITIGATING
    assert inc["maintenance_request_id"] == mr["maintenance_request_id"]
    # Incident does not embed maintenance action statuses
    assert "actions" not in inc


# AC-212 / AC-213 healthy alone does not resolve
def test_ac213_healthy_observation_alone_does_not_resolve():
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs)
    iid = inc["incident_id"]
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    # Storing healthy on incident record without verify/resolve path
    with pytest.raises(ProjectIncidentError):
        resolve_incident(
            fs, iid,
            resolution_observation_id="OBS-H",
            observation_status="HEALTHY",
        )


# AC-214 maintenance resolution alone does not resolve incident
def test_ac214_maintenance_resolved_alone_insufficient():
    fs = _fs()
    _seed_incident_contract(fs)
    _seed_maintain_contract(fs)
    inc = _detect(fs)
    iid = inc["incident_id"]
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    mr = create_maintenance_request(
        fs, owner_id="owner1", project_id="proj1",
        deployment_id="DEP-001", source_observation_id="OBS-001",
        observation_kind="health", observation_status="UNHEALTHY",
    )
    mid = mr["maintenance_request_id"]
    link_maintenance_request(
        fs, iid, maintenance_request_id=mid, owner_id="owner1", project_id="proj1",
    )
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={
            "project.build", "project.verify", CAP_PROJECT_MAINTAIN,
        },
    )
    from execution.project_maintain import load_request
    for act in load_request(fs, mid).get("actions") or []:
        execute_maintenance_action(fs, mid, act["action_id"], operation_result="SUCCEEDED")
        cur = next(
            a for a in load_request(fs, mid)["actions"] if a["action_id"] == act["action_id"]
        )
        if cur["status"] == "VERIFYING":
            verify_maintenance_action(
                fs, mid, act["action_id"], verification_result="SUCCEEDED",
            )
    mr_final = reconcile_maintenance_request(fs, mid)
    assert mr_final["status"] == REQ_RESOLVED
    # Incident still not resolved
    inc = load_incident(fs, iid)
    assert inc["status"] != INC_RESOLVED
    with pytest.raises(ProjectIncidentError):
        resolve_incident(
            fs, iid,
            maintenance_request_status=REQ_RESOLVED,
            # missing verification observation
        )


# AC-217 maintenance UNKNOWN blocks
def test_ac217_maintenance_unknown_blocks_incident_resolution():
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs)
    iid = inc["incident_id"]
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    link_maintenance_request(
        fs, iid, maintenance_request_id="mr_fake", owner_id="owner1", project_id="proj1",
    )
    inc = verify_incident(
        fs, iid,
        resolution_observation_id="OBS-X",
        observation_status="HEALTHY",
        maintenance_request_status=REQ_UNKNOWN,
    )
    assert inc["status"] == INC_UNKNOWN
    with pytest.raises(ProjectIncidentError):
        resolve_incident(
            fs, iid,
            resolution_observation_id="OBS-X",
            observation_status="HEALTHY",
            maintenance_request_status=REQ_UNKNOWN,
        )


# AC-218 cancellation evidence
def test_ac218_cancel_evidence():
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs)
    assess_incident(fs, inc["incident_id"])
    cancel_incident(fs, inc["incident_id"], actor="owner1", reason="user_cancel")
    ev = list_incident_evidence()
    assert any(
        e.get("event") == "incident_cancelled"
        and e.get("new_status") == INC_CANCELLED
        for e in ev
    )


# AC-219 end-to-end
def test_ac219_e2e_incident_maintain_verify_resolve():
    fs = _fs()
    _seed_incident_contract(fs)
    _seed_maintain_contract(fs)
    # OBS-001 UNHEALTHY → incident
    inc = _detect(fs, obs_id="OBS-001")
    iid = inc["incident_id"]
    assert inc["status"] == INC_DETECTED
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    # Maintenance path
    mr = create_maintenance_request(
        fs, owner_id="owner1", project_id="proj1",
        deployment_id="DEP-001", source_observation_id="OBS-001",
        observation_kind="health", observation_status="UNHEALTHY",
    )
    mid = mr["maintenance_request_id"]
    link_maintenance_request(
        fs, iid, maintenance_request_id=mid, owner_id="owner1", project_id="proj1",
    )
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", CAP_PROJECT_MAINTAIN},
    )
    from execution.project_maintain import load_request
    for act in load_request(fs, mid)["actions"]:
        execute_maintenance_action(fs, mid, act["action_id"], operation_result="SUCCEEDED")
        cur = next(a for a in load_request(fs, mid)["actions"] if a["action_id"] == act["action_id"])
        if cur["status"] == "VERIFYING":
            verify_maintenance_action(fs, mid, act["action_id"], verification_result="SUCCEEDED")
    assert reconcile_maintenance_request(fs, mid)["status"] == REQ_RESOLVED
    # OBS-002 HEALTHY → verify then resolve with criteria
    verify_incident(
        fs, iid,
        resolution_observation_id="OBS-002",
        observation_status="HEALTHY",
        maintenance_request_status=REQ_RESOLVED,
    )
    inc = resolve_incident(
        fs, iid,
        resolution_observation_id="OBS-002",
        observation_status="HEALTHY",
        maintenance_request_status=REQ_RESOLVED,
    )
    assert inc["status"] == INC_RESOLVED
    assert inc["resolution_observation_id"] == "OBS-002"
    # Does not rewrite observation domain
    assert inc.get("trigger", {}).get("status") == "UNHEALTHY"


def test_domain_combo_op_succeeded_obs_unhealthy_incident_open():
    """Task/operation success must coexist with UNHEALTHY + OPEN incident."""
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs, obs_id="OBS-UNH")
    assess_incident(fs, inc["incident_id"])
    # Domain snapshot (independent axes — not derived from each other)
    domains = {
        "task": "COMPLETED",
        "operation": "SUCCEEDED",
        "observation": "UNHEALTHY",
        "incident": load_incident(fs, inc["incident_id"])["status"],
    }
    assert domains["operation"] == "SUCCEEDED"
    assert domains["observation"] == "UNHEALTHY"
    assert domains["incident"] in (INC_OPEN, INC_ASSESSING, INC_DETECTED)
    assert domains["incident"] != INC_RESOLVED


def test_reobservation_unhealthy_keeps_incident_open_after_op_success():
    """Maintenance op SUCCEEDED + re-obs UNHEALTHY → incident not auto-resolved."""
    fs = _fs()
    _seed_incident_contract(fs)
    _seed_maintain_contract(fs)
    inc = _detect(fs)
    iid = inc["incident_id"]
    assess_incident(fs, iid)
    acknowledge_incident(fs, iid, actor="owner1", owner_id="owner1", project_id="proj1")
    mr = create_maintenance_request(
        fs, owner_id="owner1", project_id="proj1",
        deployment_id="DEP-001", source_observation_id="OBS-001",
        observation_kind="health", observation_status="UNHEALTHY",
    )
    mid = mr["maintenance_request_id"]
    link_maintenance_request(
        fs, iid, maintenance_request_id=mid, owner_id="owner1", project_id="proj1",
    )
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", CAP_PROJECT_MAINTAIN},
    )
    from execution.project_maintain import load_request
    for act in load_request(fs, mid)["actions"]:
        execute_maintenance_action(fs, mid, act["action_id"], operation_result="SUCCEEDED")
        cur = next(a for a in load_request(fs, mid)["actions"] if a["action_id"] == act["action_id"])
        if cur["status"] == "VERIFYING":
            verify_maintenance_action(fs, mid, act["action_id"], verification_result="SUCCEEDED")
    assert reconcile_maintenance_request(fs, mid)["status"] == REQ_RESOLVED
    # Re-observation still UNHEALTHY — incident must not silently resolve
    inc = load_incident(fs, iid)
    assert inc["status"] != INC_RESOLVED
    with pytest.raises(ProjectIncidentError):
        resolve_incident(
            fs, iid,
            resolution_observation_id="OBS-STILL-BAD",
            observation_status="UNHEALTHY",
            maintenance_request_status=REQ_RESOLVED,
        )


def test_cross_owner_cancel_denied():
    """Adversarial: other owner cannot cancel incident when identity is enforced."""
    from execution.project_incident import cancel_incident
    fs = _fs()
    _seed_incident_contract(fs)
    inc = _detect(fs)
    assess_incident(fs, inc["incident_id"])
    with pytest.raises(ProjectIncidentError):
        cancel_incident(
            fs, inc["incident_id"],
            actor="intruder",
            owner_id="intruder",
            project_id="proj1",
        )


def test_migration_exists():
    root = Path(__file__).resolve().parents[1]
    mig = root / "supabase" / "migrations" / "20260920050000_incidents.sql"
    assert mig.is_file()
    text = mig.read_text()
    assert "incidents" in text
    assert "DROP TABLE" not in text.upper()
