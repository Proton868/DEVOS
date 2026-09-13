"""Verification must not pass on agent claims alone."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_files_changed_only_does_not_pass_without_fileservice(monkeypatch):
    from brain import orchestration_verify as ov

    async def _run():
        # Force FileService import failure path by patching
        class Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("no fs")

        monkeypatch.setattr(ov, "verify_workspace_artifacts", ov.verify_workspace_artifacts)
        # Call with files_changed only — FileService may or may not exist in env.
        # We assert: if FileService fails, passed must be False.
        import execution.files as files_mod
        monkeypatch.setattr(files_mod, "FileService", Boom)
        ev = await ov.verify_workspace_artifacts(
            user_id="u1",
            workspace_id="default",
            goal="build a shoe website landing page",
            files_changed=[{"path": "index.html"}],
        )
        assert ev.get("passed") is False
        assert ev.get("weak") is True or any("FileService" in e for e in (ev.get("errors") or []))

    await _run()


def test_mission_truth_failed_status():
    from brain.nuha_bridge import mission_truth
    assert mission_truth("failed")["ok"] is False
    assert mission_truth("completed")["ok"] is True
    # status wins over ok=True
    assert mission_truth("failed", explicit_ok=True)["ok"] is False
