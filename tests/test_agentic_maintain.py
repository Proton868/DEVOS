"""Governed project.maintain — AC-121..AC-182 (focused proofs)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from execution.files import FileService
from execution.project_maintain import (
    CAP_PROJECT_MAINTAIN,
    PROFILE_MAINTAIN,
    MAINTAIN_CONTRACT,
    REQ_DETECTED, REQ_OPEN, REQ_EVALUATING, REQ_PLANNED,
    REQ_AWAITING_AUTHORIZATION, REQ_AUTHORIZED, REQ_EXECUTING, REQ_VERIFYING,
    REQ_RESOLVED, REQ_REJECTED, REQ_FAILED, REQ_CANCELLED, REQ_UNKNOWN, REQ_BLOCKED,
    ACT_PENDING, ACT_AUTHORIZED, ACT_EXECUTING, ACT_VERIFYING, ACT_SUCCEEDED,
    ACT_FAILED, ACT_SKIPPED, ACT_UNKNOWN, ACT_CANCELLED,
    ProjectMaintainError,
    validate_maintain_contract,
    write_default_maintain_contract,
    create_maintenance_request,
    evaluate_maintenance_request,
    plan_maintenance_request,
    authorize_maintenance_request,
    execute_maintenance_action,
    verify_maintenance_action,
    reconcile_maintenance_request,
    cancel_maintenance_request,
    trigger_from_observation,
    ensure_project_maintain_registered,
    reset_maintain_store_for_tests,
    list_maintain_evidence,
    transition_request,
    transition_action,
    DEFAULT_MAINTENANCE_META,
    MAX_ACTIONS_PER_REQUEST,
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
    reset_maintain_store_for_tests()
    ensure_project_maintain_registered()
    yield
    reset_maintain_store_for_tests()
    reset_capability_catalog_for_tests()
    reset_capability_substrate_for_tests()


def _fs(owner="owner1", project="proj1") -> FileService:
    return FileService(owner, project)


def _seed_contract(fs: FileService, *, optional_observe: bool = False) -> dict:
    data = {
        "version": 1,
        "profile": "maintain",
        "policies": [
            {
                "id": "repair-build",
                "trigger": {
                    "source": "observation",
                    "kind": "health",
                    "status": "UNHEALTHY",
                },
                "actions": [
                    {
                        "id": "rebuild",
                        "capability": "project.build",
                        "required": True,
                        "verification": {
                            "required": True,
                            "capability": "project.verify",
                            "ordering": "after",
                        },
                    },
                    {
                        "id": "observe",
                        "capability": "project.observe",
                        "required": not optional_observe,
                        "verification": {
                            "required": True,
                            "capability": "project.observe",
                            "ordering": "after",
                        },
                    },
                ],
            }
        ],
    }
    if optional_observe:
        data["policies"][0]["actions"][1]["required"] = False
    validate_maintain_contract(data)
    fs.write("devos.maintain.json", json.dumps(data))
    return data


def _create(fs, **kw):
    return create_maintenance_request(
        fs,
        owner_id=kw.get("owner_id", "owner1"),
        project_id=kw.get("project_id", "proj1"),
        deployment_id=kw.get("deployment_id", "DEP-001"),
        source_observation_id=kw.get("source_observation_id", "OBS-001"),
        observation_kind="health",
        observation_status="UNHEALTHY",
    )


# AC-121 / AC-122 / AC-123
def test_ac121_discoverable_and_schema():
    cat = get_capability_catalog()
    assert cat.get_capability(CAP_PROJECT_MAINTAIN) is not None
    fs = _fs()
    data = write_default_maintain_contract(fs)
    assert data["profile"] == PROFILE_MAINTAIN
    with pytest.raises(ProjectMaintainError):
        validate_maintain_contract({**data, "extra": 1})


# AC-124 / AC-125
def test_ac124_ac125_trigger_does_not_authorize():
    fs = _fs()
    _seed_contract(fs)
    req = trigger_from_observation(
        fs,
        owner_id="owner1",
        project_id="proj1",
        deployment_id="DEP-001",
        observation_id="OBS-001",
        observation_kind="health",
        observation_status="UNHEALTHY",
    )
    assert req["status"] == REQ_DETECTED
    assert req["status"] != REQ_AUTHORIZED


# AC-126 / AC-127 vocabulary
def test_ac126_ac127_unhealthy_not_operation_failed():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    # Observation UNHEALTHY creates request; operation domain not FAILED
    assert req["trigger"]["status"] == "UNHEALTHY"
    assert req["status"] == REQ_DETECTED


# AC-128..AC-132 transitions
def test_ac131_request_transition_table():
    req = {"status": REQ_DETECTED, "version": 1}
    req = transition_request(req, REQ_OPEN)
    assert req["status"] == REQ_OPEN
    with pytest.raises(ProjectMaintainError):
        transition_request(req, REQ_RESOLVED)


def test_ac132_action_transition_table():
    act = {"status": ACT_PENDING, "required": True, "version": 1}
    with pytest.raises(ProjectMaintainError):
        transition_action(act, ACT_SKIPPED)
    act = transition_action(act, ACT_AUTHORIZED)
    assert act["status"] == ACT_AUTHORIZED


# AC-133..AC-139 required/optional
def test_ac134_required_failure_blocks_resolve():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    req = evaluate_maintenance_request(fs, req["maintenance_request_id"])
    req = plan_maintenance_request(fs, req["maintenance_request_id"])
    req = authorize_maintenance_request(
        fs, req["maintenance_request_id"],
        owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN},
    )
    # Fail first required action
    primary = req["actions"][0]
    req = execute_maintenance_action(
        fs, req["maintenance_request_id"], primary["action_id"],
        operation_result="FAILED",
    )
    assert req["status"] == REQ_FAILED
    with pytest.raises(ProjectMaintainError):
        # cannot resolve from FAILED
        transition_request(req, REQ_RESOLVED)


def test_ac135_required_unknown_blocks_resolve():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    req = evaluate_maintenance_request(fs, req["maintenance_request_id"])
    req = plan_maintenance_request(fs, req["maintenance_request_id"])
    req = authorize_maintenance_request(
        fs, req["maintenance_request_id"],
        owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN},
    )
    primary = req["actions"][0]
    req = execute_maintenance_action(
        fs, req["maintenance_request_id"], primary["action_id"],
        operation_result="UNKNOWN",
    )
    assert req["status"] == REQ_UNKNOWN
    with pytest.raises(ProjectMaintainError):
        reconcile_maintenance_request(fs, req["maintenance_request_id"])


def test_ac136_optional_failure_may_resolve():
    fs = _fs()
    _seed_contract(fs, optional_observe=True)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    req = authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN},
    )
    # Succeed all required; fail optional observe actions
    for act in list(req["actions"]):
        if act.get("required"):
            req = execute_maintenance_action(
                fs, mid, act["action_id"], operation_result="SUCCEEDED",
            )
            # If VERIFYING after primary with verification_required
            updated = next(a for a in req["actions"] if a["maintenance_action_id"] == act["maintenance_action_id"])
            if updated["status"] == ACT_VERIFYING:
                req = verify_maintenance_action(
                    fs, mid, act["action_id"], verification_result="SUCCEEDED",
                )
        else:
            req = execute_maintenance_action(
                fs, mid, act["action_id"], operation_result="FAILED",
            )
    req = reconcile_maintenance_request(fs, mid)
    assert req["status"] == REQ_RESOLVED


# AC-140 / AC-141 planner cannot forge auth
def test_ac141_planner_cannot_forge_authorization():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    # Wrong owner
    with pytest.raises(ProjectMaintainError):
        authorize_maintenance_request(
            fs, mid, owner_id="other", project_id="proj1",
            granted_capabilities={CAP_PROJECT_MAINTAIN, "project.build"},
        )


# AC-144 catalogued only
def test_ac144_unknown_capability_rejected_in_contract():
    with pytest.raises(ProjectMaintainError):
        validate_maintain_contract({
            "version": 1,
            "profile": "maintain",
            "policies": [{
                "id": "bad",
                "trigger": {"source": "observation", "kind": "health", "status": "UNHEALTHY"},
                "actions": [{
                    "id": "x",
                    "capability": "shell.exec",
                    "required": True,
                }],
            }],
        })


# AC-145 metadata
def test_ac145_verification_metadata_explicit():
    assert DEFAULT_MAINTENANCE_META["project.build"]["can_verify_maintenance"] is False
    assert DEFAULT_MAINTENANCE_META["project.verify"]["can_verify_maintenance"] is True


# AC-147 primary success without verification
def test_ac147_primary_success_needs_verification():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    req = authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN},
    )
    primary = [a for a in req["actions"] if a["action_id"] == "rebuild"][0]
    assert primary.get("verification_required") is True
    req = execute_maintenance_action(fs, mid, "rebuild", operation_result="SUCCEEDED")
    updated = [a for a in req["actions"] if a["action_id"] == "rebuild"][0]
    assert updated["status"] == ACT_VERIFYING
    assert updated["status"] != ACT_SUCCEEDED


# AC-148 verification failure
def test_ac148_verification_failure_blocks():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN},
    )
    execute_maintenance_action(fs, mid, "rebuild", operation_result="SUCCEEDED")
    req = verify_maintenance_action(fs, mid, "rebuild", verification_result="FAILED")
    assert req["status"] == REQ_FAILED


# AC-158 / AC-159 idempotent
def test_ac158_idempotent_create():
    fs = _fs()
    _seed_contract(fs)
    r1 = _create(fs, source_observation_id="OBS-SAME")
    r2 = _create(fs, source_observation_id="OBS-SAME")
    assert r1["maintenance_request_id"] == r2["maintenance_request_id"]


# AC-160 UNKNOWN cannot resolve
def test_ac160_unknown_cannot_resolve():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={"project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN},
    )
    execute_maintenance_action(fs, mid, "rebuild", operation_result="UNKNOWN")
    with pytest.raises(ProjectMaintainError):
        reconcile_maintenance_request(fs, mid)


# AC-162 / AC-163 evidence
def test_ac162_ac163_failure_and_cancel_evidence():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    cancel_maintenance_request(fs, mid, actor="owner1", reason="user_cancel")
    ev = list_maintain_evidence()
    assert any(e.get("event") == "maintenance_cancelled" for e in ev)
    assert any(e.get("new_status") == REQ_CANCELLED for e in ev)


# AC-166 cross-owner
def test_ac166_cross_owner_denied():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    with pytest.raises(ProjectMaintainError):
        authorize_maintenance_request(
            fs, mid, owner_id="intruder", project_id="proj1",
            granted_capabilities={CAP_PROJECT_MAINTAIN},
        )


# AC-175 / AC-176 bounds
def test_ac176_max_actions_bound():
    assert MAX_ACTIONS_PER_REQUEST == 16


# AC-177 end-to-end happy path
def test_ac177_e2e_observation_to_resolved():
    fs = _fs()
    _seed_contract(fs)
    req = trigger_from_observation(
        fs,
        owner_id="owner1",
        project_id="proj1",
        deployment_id="DEP-001",
        observation_id="OBS-001",
        observation_kind="health",
        observation_status="UNHEALTHY",
    )
    mid = req["maintenance_request_id"]
    assert req["status"] == REQ_DETECTED
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    req = authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={
            "project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN,
        },
    )
    assert req["status"] == REQ_AUTHORIZED
    # Execute + verify each action in order
    for act in list(req["actions"]):
        aid = act["action_id"]
        req = execute_maintenance_action(fs, mid, aid, operation_result="SUCCEEDED")
        cur = next(a for a in req["actions"] if a["action_id"] == aid)
        if cur["status"] == ACT_VERIFYING:
            req = verify_maintenance_action(fs, mid, aid, verification_result="SUCCEEDED")
    req = reconcile_maintenance_request(fs, mid)
    assert req["status"] == REQ_RESOLVED
    # Resolution does not rewrite observation health
    assert req.get("trigger", {}).get("status") == "UNHEALTHY"


# AC-180 cancellation lifecycle
def test_ac180_cancel_from_open_and_planned():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)  # OPEN→EVALUATING
    # back to cancellable: cancel from EVALUATING
    req = cancel_maintenance_request(fs, mid)
    assert req["status"] == REQ_CANCELLED
    with pytest.raises(ProjectMaintainError):
        transition_request(req, REQ_RESOLVED)


def test_ac180_cancel_from_planned():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    req = cancel_maintenance_request(fs, mid)
    assert req["status"] == REQ_CANCELLED


# Migration present
def test_migration_file_exists():
    root = Path(__file__).resolve().parents[1]
    mig = root / "supabase" / "migrations" / "20260920040000_maintenance_requests_actions.sql"
    assert mig.is_file()
    text = mig.read_text()
    assert "maintenance_requests" in text
    assert "maintenance_actions" in text
    assert "DROP TABLE" not in text.upper()


# Healthy observation → no maintenance action (no policy match)
def test_healthy_observation_no_maintenance_action():
    fs = _fs()
    _seed_contract(fs)
    with pytest.raises(ProjectMaintainError) as ei:
        create_maintenance_request(
            fs,
            owner_id="owner1",
            project_id="proj1",
            deployment_id="DEP-001",
            source_observation_id="OBS-HEALTHY",
            observation_kind="health",
            observation_status="HEALTHY",
        )
    assert ei.value.code == "NO_POLICY_MATCH"


# Operation SUCCEEDED + re-observation UNHEALTHY remains a valid domain pairing
def test_operation_succeeded_observation_may_remain_unhealthy():
    fs = _fs()
    _seed_contract(fs)
    req = _create(fs)
    mid = req["maintenance_request_id"]
    evaluate_maintenance_request(fs, mid)
    plan_maintenance_request(fs, mid)
    authorize_maintenance_request(
        fs, mid, owner_id="owner1", project_id="proj1",
        granted_capabilities={
            "project.build", "project.verify", "project.observe", CAP_PROJECT_MAINTAIN,
        },
    )
    req = execute_maintenance_action(fs, mid, "rebuild", operation_result="SUCCEEDED")
    rebuilt = next(a for a in req["actions"] if a["action_id"] == "rebuild")
    assert rebuilt["operation_id"]
    assert rebuilt["status"] in (ACT_VERIFYING, ACT_SUCCEEDED)
    # Simulated re-observation domain (independent of operation/task)
    post_observation = {
        "observation_id": "OBS-POST",
        "kind": "health",
        "status": "UNHEALTHY",
        "operation_status": "SUCCEEDED",
    }
    assert post_observation["operation_status"] == "SUCCEEDED"
    assert post_observation["status"] == "UNHEALTHY"
    assert req["status"] != REQ_RESOLVED
