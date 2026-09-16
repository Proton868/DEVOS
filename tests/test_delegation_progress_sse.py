"""Real progress hooks from run_delegated_mission (not fabricated SSE)."""
from __future__ import annotations

import asyncio
from pathlib import Path


def test_chat_source_streams_progress_queue():
    src = Path("api/routes/chat.py").read_text()
    assert "on_progress=_on_mission_progress" in src
    assert "progress_q" in src
    assert "_mission_done" in src
    assert "run_delegated_mission(" in src


def test_delegation_source_emits_real_lifecycle_phases():
    src = Path("brain/delegation.py").read_text()
    assert "async def _emit_progress" in src
    assert "on_progress=None" in src
    for phase in (
        "mission_task_assigned",
        "a2a_delegate_sent",
        "specialist_execution",
        "ponytail",
    ):
        assert phase in src, f"missing phase {phase}"
    assert "validation_started" in src
    assert "worker_completed" in src


def test_emit_progress_invokes_async_callback():
    from brain.delegation import _emit_progress

    seen = []

    async def cb(payload):
        seen.append(payload)

    asyncio.run(_emit_progress(cb, {"status": "agent_progress", "phase": "mission_task_assigned"}))
    assert seen and seen[0]["phase"] == "mission_task_assigned"


def test_emit_progress_swallows_callback_errors():
    from brain.delegation import _emit_progress

    async def bad(_payload):
        raise RuntimeError("progress sink down")

    asyncio.run(_emit_progress(bad, {"status": "agent_progress"}))
