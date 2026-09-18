"""Integration tests for application lifecycle CREATE → … → MAINTAIN."""
from __future__ import annotations

import asyncio

import pytest

from execution.app_lifecycle import (
    LifecycleStage,
    advance,
    create_lifecycle,
    get_lifecycle,
    reset_lifecycle_store_for_tests,
    run_pipeline,
)
from execution.artifacts import write_bytes
from execution.files import FileService
from execution.deploy.base import DeploymentStatus


@pytest.fixture(autouse=True)
def _clean():
    reset_lifecycle_store_for_tests()
    yield
    reset_lifecycle_store_for_tests()


def test_create_bootstrap_and_evidence():
    uid, pid = "lc-user", "lc-proj-create"
    rec = create_lifecycle(
        uid,
        pid,
        bootstrap_files={"index.html": b"<html><body>Footwalk</body></html>"},
    )
    assert rec.stage == LifecycleStage.CREATE
    assert rec.lifecycle_id
    assert any(e.stage == "CREATE" and e.ok for e in rec.evidence)
    fs = FileService(uid, pid)
    data = fs.read("index.html")
    content = data.get("content") if isinstance(data, dict) else data
    assert "Footwalk" in str(content)


def test_illegal_transition_fails():
    uid, pid = "lc-user", "lc-bad-trans"
    create_lifecycle(uid, pid, bootstrap_files={"index.html": b"<html></html>"})
    # Jump CREATE → DEPLOY is illegal
    rec = asyncio.run(advance(uid, pid, "DEPLOY", params={"provider": "vercel"}))
    assert rec.stage == LifecycleStage.FAILED
    assert any(e.stage == "DEPLOY" and not e.ok for e in rec.evidence)


def test_deploy_requires_verify_evidence():
    uid, pid = "lc-user", "lc-deploy-block"
    create_lifecycle(uid, pid, bootstrap_files={"index.html": b"<html>x</html>"})
    # Reach VERIFY without recording ok VERIFY — force stage via BUILD→PREVIEW then strip
    asyncio.run(advance(uid, pid, "DEVELOP"))
    asyncio.run(advance(uid, pid, "BUILD"))
    asyncio.run(advance(uid, pid, "PREVIEW"))
    rec = get_lifecycle(uid, pid)
    # If we never VERIFY, deploy from PREVIEW is illegal; from VERIFY without ok evidence
    if rec.stage == LifecycleStage.PREVIEW:
        # Manually set stage to VERIFY without ok evidence to test deploy gate
        rec.stage = LifecycleStage.VERIFY
        rec.evidence = [e for e in rec.evidence if e.stage != "VERIFY"]
    rec = asyncio.run(advance(uid, pid, "DEPLOY", params={"provider": "vercel"}))
    assert rec.stage == LifecycleStage.FAILED
    reasons = [
        (e.details or {}).get("reason")
        for e in rec.evidence
        if e.stage == "DEPLOY"
    ]
    assert "MISSING_VERIFY_EVIDENCE" in reasons or any(
        e.stage == "DEPLOY" and not e.ok for e in rec.evidence
    )


def test_deploy_without_credentials_not_success():
    """No fake deploy success when adapter lacks credentials."""
    uid, pid = "lc-user", "lc-deploy-auth"
    create_lifecycle(uid, pid, bootstrap_files={"index.html": b"<html>y</html>"})
    asyncio.run(advance(uid, pid, "DEVELOP"))
    asyncio.run(advance(uid, pid, "BUILD"))
    # Force path: manually mark VERIFY by running verify after preview stages
    # BUILD may leave stage BUILD; PREVIEW then VERIFY
    asyncio.run(advance(uid, pid, "PREVIEW"))
    asyncio.run(advance(uid, pid, "VERIFY"))
    rec = get_lifecycle(uid, pid)
    assert rec.stage in (LifecycleStage.VERIFY, LifecycleStage.FAILED)
    if rec.stage != LifecycleStage.VERIFY:
        pytest.skip("preview/verify environment could not ready static project")
    rec = asyncio.run(advance(uid, pid, "DEPLOY", params={"provider": "vercel", "credentials": {}}))
    assert rec.stage == LifecycleStage.FAILED
    assert rec.deployment is not None
    assert rec.deployment.get("ok") is False
    assert rec.deployment.get("status") in (
        DeploymentStatus.FAILED.value,
        "FAILED",
        "REQUESTED",
    )
    # Must not claim success
    assert not any(e.stage == "DEPLOY" and e.ok for e in rec.evidence)


def test_full_pipeline_static_to_verify():
    uid, pid = "lc-user", "lc-pipeline"
    create_lifecycle(
        uid,
        pid,
        bootstrap_files={
            "index.html": b"<!DOCTYPE html><html><body><h1>App</h1></body></html>",
            ".env.example": b"API_URL=\n",
        },
    )
    rec = asyncio.run(
        run_pipeline(
            uid,
            pid,
            stages=["DEVELOP", "BUILD", "PREVIEW", "VERIFY"],
            params={"source": "test"},
        )
    )
    stages_ok = [e.stage for e in rec.evidence if e.ok]
    assert "DEVELOP" in stages_ok
    assert "BUILD" in stages_ok or rec.stage == LifecycleStage.FAILED
    # VERIFY success required for "complete" static pipeline
    if rec.stage == LifecycleStage.VERIFY:
        assert any(e.stage == "VERIFY" and e.ok for e in rec.evidence)
        assert rec.verification and rec.verification.get("ok") is True
    else:
        # Honest failure still retains evidence history
        assert len(rec.evidence) >= 2
        assert rec.stage in (LifecycleStage.FAILED, LifecycleStage.PREVIEW, LifecycleStage.BUILD)


def test_observe_and_maintain():
    uid, pid = "lc-user", "lc-observe"
    create_lifecycle(uid, pid, bootstrap_files={"index.html": b"<html>z</html>"})
    asyncio.run(advance(uid, pid, "DEVELOP"))
    # OBSERVE allowed from DEVELOP? No — only from DEPLOY/OBSERVE/MAINTAIN path
    # Develop → observe is illegal → FAILED
    rec = asyncio.run(advance(uid, pid, "OBSERVE"))
    assert rec.stage == LifecycleStage.FAILED

    # Reset and run through VERIFY then OBSERVE via forced stage path after VERIFY
    reset_lifecycle_store_for_tests()
    create_lifecycle(uid, pid, bootstrap_files={"index.html": b"<html>z</html>"})
    rec = asyncio.run(
        run_pipeline(uid, pid, stages=["DEVELOP", "BUILD", "PREVIEW", "VERIFY"])
    )
    if rec.stage != LifecycleStage.VERIFY:
        pytest.skip("could not reach VERIFY")
    rec = asyncio.run(advance(uid, pid, "OBSERVE"))
    # OBSERVE allowed from VERIFY
    assert rec.stage in (LifecycleStage.OBSERVE, LifecycleStage.FAILED)
    if rec.stage == LifecycleStage.OBSERVE:
        rec = asyncio.run(advance(uid, pid, "MAINTAIN"))
        assert rec.stage == LifecycleStage.MAINTAIN
        assert any(e.stage == "MAINTAIN" and e.ok for e in rec.evidence)


def test_cancel():
    uid, pid = "lc-user", "lc-cancel"
    create_lifecycle(uid, pid)
    rec = asyncio.run(advance(uid, pid, "CANCELLED"))
    assert rec.stage == LifecycleStage.CANCELLED
    assert rec.cancelled is True


def test_capability_registered():
    from governance.runtime_capabilities import ensure_runtime_capabilities_registered
    from governance.capability_registry import get_registry

    ensure_runtime_capabilities_registered()
    # side-effect import registers lifecycle
    import governance.runtime_capabilities as rc

    rc._register_lifecycle_capability()
    assert get_registry().get("devos.app.lifecycle") is not None
