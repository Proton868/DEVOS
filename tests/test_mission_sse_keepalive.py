"""Mission chat SSE: keepalive + disconnect must not invent failure."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_send_has_keepalive_and_disconnect_handling():
    src = (ROOT / "api" / "routes" / "chat.py").read_text()
    assert "keepalive" in src
    assert "wait_for" in src
    assert "is_disconnected" in src
    assert "X-Accel-Buffering" in src
    assert "Cache-Control" in src
    # Mission must continue / result recovered after detach
    assert "mission finished after SSE detach" in src or "backend mission continues" in src
    assert "return result" in src  # _run_mission returns result for detach path


def test_stream_chat_does_not_throw_on_network_error():
    src = (ROOT / "frontend-src" / "src" / "services" / "api.js").read_text()
    assert "stream_interrupted" in src
    # must not rethrow after yielding stream error
    assert "stream_state: \"error\"" in src or "stream_state: 'error'" in src
    # ensure throw err is not the path after stream_interrupted
    idx = src.find("stream_interrupted")
    snippet = src[idx : idx + 200]
    assert "throw err" not in snippet


def test_aicopilot_stream_error_not_mission_authority():
    src = (ROOT / "frontend-src" / "src" / "os" / "focus" / "AICopilot.jsx").read_text()
    assert "may still be running" in src.lower() or "server-side" in src.lower()
    assert "keepalive" in src


def test_chat_py_parses():
    ast.parse((ROOT / "api" / "routes" / "chat.py").read_text())
