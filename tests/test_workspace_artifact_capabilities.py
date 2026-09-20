"""Governed workspace & artifact capability tests."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from execution.workspace_capabilities import (
    ALL_CAPS,
    CAP_ARTIFACT_DELETE,
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_STAT,
    CAP_ARTIFACT_WRITE,
    CAP_WORKSPACE_LIST,
    MAX_WRITE_BYTES_CAP,
    ensure_workspace_capabilities_registered,
    execute_workspace_capability,
    WorkspaceCapabilityError,
)
from execution.files import FileService, PathViolation, PROJECTS_DIR
from execution.artifacts import content_hash, is_secret_path
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
    run_agent_turn,
    run_agent_until_terminal,
    set_completion_contract,
)
from brain.agentic_llm_planner import FakeLLMProvider, make_llm_planner


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    # Isolate projects dir
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr("execution.files.PROJECTS_DIR", root)
    monkeypatch.setattr("execution.workspace_capabilities.FileService", FileService)
    reset_capability_substrate_for_tests()
    reset_capability_catalog_for_tests()
    reset_agent_task_store_for_tests()
    ensure_workspace_capabilities_registered()
    yield
    reset_agent_task_store_for_tests()
    reset_capability_catalog_for_tests()
    reset_capability_substrate_for_tests()


def _ctx(owner="owner1", project="proj1", grants=None):
    return InvocationContext(
        tenant_id="ten1",
        owner_id=owner,
        granted_capabilities=set(grants or ALL_CAPS),
        actor_type="agent",
        actor_id=owner,
        surface="test",
        client_supplied_grants=False,
        metadata={"project_id": project},
    )


def _req(cid, inputs, ctx=None):
    return InvocationRequest(
        capability_id=cid,
        inputs=inputs,
        context=ctx or _ctx(),
    )


def _contract(cid):
    return CapabilityContract(
        identity=cid,
        version="1",
        name=cid,
        category="workspace",
        description=cid,
    )


def _run(coro):
    return asyncio.run(coro)


# ── Path security ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "/etc/passwd",
    "../escape",
    "foo/../../escape",
    "..\\escape",
    "foo/%2e%2e/bar",
    "foo\x00bar",
    "C:\\Windows\\System32",
])
def test_path_traversal_rejected(bad):
    with pytest.raises((WorkspaceCapabilityError, PathViolation, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": bad}),
        ))


def test_symlink_escape_rejected(tmp_path):
    fs = FileService("owner1", "proj1")
    # Create symlink pointing outside workspace
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret-host-data")
    link = fs.root / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not supported")
    # Reading via symlink name should fail closed
    with pytest.raises((WorkspaceCapabilityError, PathViolation, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": "escape"}),
        ))


def test_workspace_root_not_from_planner():
    ctx = _ctx()
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": "a.txt", "workspace_root": "/etc"}, ctx),
        ))


def test_owner_tenant_fields_rejected():
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": "a.txt", "owner_id": "victim"}),
        ))


# ── Happy path ───────────────────────────────────────────────────────────────

def test_write_read_list_stat_delete_cycle():
    ctx = _ctx()
    # write
    w = _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_WRITE),
        _req(CAP_ARTIFACT_WRITE, {"path": "src/hello.txt", "content": "hello world"}, ctx),
    ))
    assert w["digest"]
    assert w["size"] == len("hello world")
    assert w["evidence"]["after_digest"] == w["digest"]
    # list
    listing = _run(execute_workspace_capability(
        _contract(CAP_WORKSPACE_LIST),
        _req(CAP_WORKSPACE_LIST, {"path": "src"}, ctx),
    ))
    paths = [e.get("path") for e in listing["entries"]]
    assert any("hello" in str(p) for p in paths)
    # stat
    st = _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_STAT),
        _req(CAP_ARTIFACT_STAT, {"path": "src/hello.txt"}, ctx),
    ))
    assert st["digest"] == w["digest"]
    # read
    r = _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_READ),
        _req(CAP_ARTIFACT_READ, {"path": "src/hello.txt"}, ctx),
    ))
    assert r["content"] == "hello world"
    assert r["digest"] == w["digest"]
    # delete
    d = _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_DELETE),
        _req(CAP_ARTIFACT_DELETE, {"path": "src/hello.txt"}, ctx),
    ))
    assert d["deleted"] is True
    assert d["before_digest"] == w["digest"]
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": "src/hello.txt"}, ctx),
        ))


def test_expected_digest_conflict():
    ctx = _ctx()
    _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_WRITE),
        _req(CAP_ARTIFACT_WRITE, {"path": "f.txt", "content": "v1"}, ctx),
    ))
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_WRITE),
            _req(CAP_ARTIFACT_WRITE, {
                "path": "f.txt",
                "content": "v2",
                "expected_digest": "0" * 64,
            }, ctx),
        ))


def test_oversized_write_rejected():
    ctx = _ctx()
    big = "x" * (MAX_WRITE_BYTES_CAP + 10)
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_WRITE),
            _req(CAP_ARTIFACT_WRITE, {"path": "big.txt", "content": big}, ctx),
        ))


def test_secret_path_content_denied():
    ctx = _ctx()
    fs = FileService("owner1", "proj1")
    # Force-write via pathlib (bypass capability) to simulate existing secret
    p = fs.root / ".env"
    p.write_text("SECRET=1")
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": ".env"}, ctx),
        ))
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_WRITE),
            _req(CAP_ARTIFACT_WRITE, {"path": ".env", "content": "x"}, ctx),
        ))


def test_forged_digest_ignored_on_write():
    ctx = _ctx()
    w = _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_WRITE),
        _req(CAP_ARTIFACT_WRITE, {
            "path": "x.txt",
            "content": "abc",
            "digest": "forged" * 10,
        }, ctx),
    ))
    assert w["digest"] == content_hash(b"abc")
    assert w["digest"] != "forged" * 10


def test_catalog_registers_capabilities():
    ensure_workspace_capabilities_registered()
    cat = get_capability_catalog()
    for cid in ALL_CAPS:
        e = cat.get_capability(cid)
        assert e is not None, cid
        assert e.requires_authorization is True


def test_substrate_authorize_and_execute():
    ensure_workspace_capabilities_registered()
    sub = get_capability_substrate()
    ctx = _ctx(grants=list(ALL_CAPS))
    req = InvocationRequest(
        capability_id=CAP_ARTIFACT_WRITE,
        inputs={"path": "a.txt", "content": "hi"},
        context=ctx,
    )
    result = _run(sub.invoke(req))
    st = result.status.value if hasattr(result.status, "value") else str(result.status)
    assert st in ("executed", "EXECUTED") or result.authorized
    assert result.outputs and result.outputs.get("digest")


def test_unprojected_capability_denied_via_task():
    ensure_workspace_capabilities_registered()
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=[CAP_WORKSPACE_LIST],  # no write
        task_input={"project_id": "proj1"},
    )
    rec = _run(request_capability(
        t,
        capability_id=CAP_ARTIFACT_WRITE,
        inputs={"path": "x.txt", "content": "nope"},
        execute=True,
    ))
    assert rec.status == "denied"


def test_multi_turn_artifact_workflow():
    """FakeLLM multi-turn: list → write → read → complete."""
    ensure_workspace_capabilities_registered()
    caps = [CAP_WORKSPACE_LIST, CAP_ARTIFACT_WRITE, CAP_ARTIFACT_READ]
    script = [
        json.dumps({"action": {"type": "capability_request", "capability": CAP_WORKSPACE_LIST, "input": {}}}),
        json.dumps({"action": {"type": "capability_request", "capability": CAP_ARTIFACT_WRITE,
                               "input": {"path": "app.txt", "content": "built-by-agent"}}}),
        json.dumps({"action": {"type": "capability_request", "capability": CAP_ARTIFACT_READ,
                               "input": {"path": "app.txt"}}}),
        json.dumps({"action": {"type": "complete", "rationale": "done"}}),
    ]
    planner = make_llm_planner(FakeLLMProvider(script=script))
    t = delegate_agent_task(
        owner_id="owner1",
        requested_capabilities=caps,
        task_input={"project_id": "proj1", "objective": "create app.txt"},
    )
    set_completion_contract(
        t,
        CompletionContract(require_structured_complete_decision=True),
    )
    t = _run(run_agent_until_terminal(t, planner=planner, max_loops=12))
    cp = checkpoint_from_task(t)
    # File must exist with expected content regardless of terminal state
    fs = FileService("owner1", "proj1")
    data = (fs.root / "app.txt").read_text()
    assert data == "built-by-agent"
    # Not stuck in EXECUTING
    assert cp.state != AgentRuntimeState.EXECUTING


def test_restart_preserves_written_artifact():
    ensure_workspace_capabilities_registered()
    ctx = _ctx()
    w = _run(execute_workspace_capability(
        _contract(CAP_ARTIFACT_WRITE),
        _req(CAP_ARTIFACT_WRITE, {"path": "persist.txt", "content": "stable"}, ctx),
    ))
    # Simulate restart: new FileService same identity
    fs = FileService("owner1", "proj1")
    assert (fs.root / "persist.txt").read_text() == "stable"
    assert content_hash((fs.root / "persist.txt").read_bytes()) == w["digest"]


def test_delete_workspace_root_rejected():
    with pytest.raises((WorkspaceCapabilityError, RuntimeError)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_DELETE),
            _req(CAP_ARTIFACT_DELETE, {"path": ""}),
        ))


def test_planner_cannot_access_filesystem_module_directly_in_capability():
    """Sanity: capability module does not expose host PROJECTS traversal via inputs."""
    with pytest.raises((WorkspaceCapabilityError, RuntimeError, PathViolation)):
        _run(execute_workspace_capability(
            _contract(CAP_ARTIFACT_READ),
            _req(CAP_ARTIFACT_READ, {"path": "../../../../../etc/passwd"}),
        ))
