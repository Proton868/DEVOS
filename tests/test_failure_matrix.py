import pytest
"""Focused failure-matrix invariants for mission truth and cancel."""
import asyncio
from brain.nuha_bridge import mission_truth, synthesize_orchestration_reply
from brain.orchestration import OrchestrationPlan, request_cancel, execute_plan, _PLANS, OrchStatus
from brain.orchestration_verify import verify_workspace_artifacts


def test_matrix_success_only_on_completed():
    for st in ("completed", "succeeded", "verified"):
        assert mission_truth(st)["ok"] is True
    for st in ("failed", "cancelled", "blocked", "running", "unknown", "waiting_for_user"):
        assert mission_truth(st)["ok"] is False


def test_matrix_synthesis_never_claims_done_on_failure():
    text = synthesize_orchestration_reply({
        "ok": False, "status": "failed", "plan_id": "p", "orchestrated": True,
    })
    low = text.lower()
    assert "failed" in low or "hit" in low or "could not" in low or "not" in low
    assert "successfully completed" not in low


@pytest.mark.asyncio
async def test_matrix_cancel_before_execute():
    async def _run():
        p = OrchestrationPlan(id="cx1", user_id="u1", goal="x", status=OrchStatus.CANCELLATION_REQUESTED.value)
        _PLANS[p.id] = p
        out = await execute_plan(p)
        assert (out.status or "").lower() == "cancelled"
        assert mission_truth(out.status)["ok"] is False
    await _run()


@pytest.mark.asyncio
async def test_matrix_verification_false_without_disk(monkeypatch):
    async def _run():
        class Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("no fs")
        import execution.files as files_mod
        monkeypatch.setattr(files_mod, "FileService", Boom)
        ev = await verify_workspace_artifacts(
            user_id="u1", workspace_id="default",
            goal="website landing", files_changed=[{"path": "index.html"}],
        )
        assert ev.get("passed") is False
    await _run()
