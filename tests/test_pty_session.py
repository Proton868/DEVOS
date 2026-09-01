"""Tests for execution/pty_session.py — persistent PTY terminal sessions."""
import asyncio
import os
import pytest
import pytest_asyncio
import time
from unittest.mock import MagicMock, patch

from execution.pty_session import PtySession, get_or_create_session, kill_session, _sessions


# ── Mock WebSocket for testing ────────────────────────────────────────────────

class MockWebSocket:
    """Simulates a WebSocket client for testing PtySession attach/detach/broadcast."""
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_json(self, data: dict):
        self.sent.append(data)

    async def close(self, code: int = 1000):
        self.closed = True


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def session():
    """Create a PtySession for a test project."""
    s = PtySession(user_id="test-user", project_id="test-project")
    yield s
    # Cleanup
    if s._started:
        await s.kill()


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the global session registry before each test."""
    _sessions.clear()
    yield
    _sessions.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPtySessionBasics:
    def test_init_sets_attributes(self):
        s = PtySession("u1", "p1")
        assert s.user_id == "u1"
        assert s.project_id == "p1"
        assert s.is_alive is False
        assert s.client_count == 0

    @pytest.mark.asyncio
    async def test_start_and_is_alive(self, session):
        await session.start()
        assert session.is_alive is True
        assert session._started is True

    @pytest.mark.asyncio
    async def test_write_and_read_output(self, session):
        await session.start()
        # Give the shell a moment to start
        await asyncio.sleep(0.2)

        # Write a command
        session.write(b"echo HELLO_PTY\n")

        # Wait for output to appear in scrollback
        await asyncio.sleep(0.5)

        # Check scrollback contains the expected output
        scrollback_text = b"".join(session._scrollback).decode("utf-8", errors="replace")
        assert "HELLO_PTY" in scrollback_text, f"Expected 'HELLO_PTY' in scrollback, got: {scrollback_text[:200]}"

    @pytest.mark.asyncio
    async def test_kill_cleans_up(self, session):
        await session.start()
        assert session.is_alive is True

        await session.kill()
        # Give the process a moment to die
        await asyncio.sleep(0.3)
        assert session.is_alive is False
        assert session._started is False


class TestScrollbackAndReplay:
    @pytest.mark.asyncio
    async def test_attach_replays_scrollback(self, session):
        await session.start()
        await asyncio.sleep(0.2)

        # Write a command to populate scrollback
        session.write(b"echo REPLAY_ME\n")
        await asyncio.sleep(0.5)

        # Verify scrollback has content
        assert len(session._scrollback) > 0

        # Attach a new client — should receive scrollback replay
        ws = MockWebSocket()
        session.attach(ws)

        # The attach method sends replay tasks; wait for them
        await asyncio.sleep(0.3)

        # The attached client should have received data messages
        data_messages = [m for m in ws.sent if m.get("type") == "data"]
        assert len(data_messages) > 0, f"No data messages received, sent={ws.sent}"

        session.detach(ws)

    @pytest.mark.asyncio
    async def test_scrollback_respects_size_limit(self, session):
        await session.start()
        await asyncio.sleep(0.2)

        # Write a lot of data to fill scrollback
        big_data = b"x" * 4096
        for _ in range(100):
            session.write(b"echo " + big_data[:100] + b"\n")

        await asyncio.sleep(0.2)

        # Scrollback should be capped at 256KB
        assert session._scrollback_size <= 256 * 1024 + 4096  # Allow one extra chunk


class TestMultiClient:
    @pytest.mark.asyncio
    async def test_two_clients_both_receive_output(self, session):
        await session.start()
        await asyncio.sleep(0.2)

        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        session.attach(ws1)
        session.attach(ws2)

        assert session.client_count == 2

        # Write a command
        session.write(b"echo MULTI_CLIENT\n")
        await asyncio.sleep(0.5)

        # Both clients should have received data
        data1 = [m for m in ws1.sent if m.get("type") == "data"]
        data2 = [m for m in ws2.sent if m.get("type") == "data"]
        assert len(data1) > 0
        assert len(data2) > 0

        session.detach(ws1)
        session.detach(ws2)
        assert session.client_count == 0


class TestIdleTimeout:
    @pytest.mark.asyncio
    async def test_is_idle_with_no_clients_and_stale(self, session):
        await session.start()
        await asyncio.sleep(0.2)

        # No clients attached, and we just wrote output so it's not idle yet
        assert session.is_idle is False

        # Manually set last_output_time to far in the past
        session._last_output_time = time.monotonic() - 99999

        assert session.is_idle is True


class TestRegistry:
    @pytest.mark.asyncio
    async def test_get_or_create_session_returns_same_session(self):
        s1 = await get_or_create_session("user-a", "proj-a")
        s2 = await get_or_create_session("user-a", "proj-a")
        assert s1 is s2

        await s1.kill()

    @pytest.mark.asyncio
    async def test_different_projects_get_different_sessions(self):
        s1 = await get_or_create_session("user-a", "proj-a")
        s2 = await get_or_create_session("user-a", "proj-b")
        assert s1 is not s2
        assert s1.root != s2.root

        await s1.kill()
        await s2.kill()

    @pytest.mark.asyncio
    async def test_kill_session_removes_from_registry(self):
        s = await get_or_create_session("user-b", "proj-b")
        assert ("user-b", "proj-b") in _sessions

        await kill_session("user-b", "proj-b")
        await asyncio.sleep(0.3)
        assert ("user-b", "proj-b") not in _sessions
        assert s.is_alive is False


class TestResize:
    @pytest.mark.asyncio
    async def test_resize_does_not_crash(self, session):
        await session.start()
        # Should not raise
        await session.resize(cols=120, rows=40)
        assert session.is_alive is True