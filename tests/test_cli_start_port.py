"""CLI start: refuse to bind a second listener when the port is occupied."""
from __future__ import annotations

import socket
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import cli


def test_port_is_in_use_false_on_free_ephemeral_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert cli.port_is_in_use("127.0.0.1", port) is False


def test_port_is_in_use_true_when_listener_exists():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert cli.port_is_in_use("127.0.0.1", port) is True
        assert cli.port_is_in_use("0.0.0.0", port) is True
    finally:
        srv.close()


def test_cmd_start_exits_zero_when_port_busy(capsys):
    args = SimpleNamespace(port=18000, host="0.0.0.0", dev=False, quiet=False)
    with patch.object(cli, "port_is_in_use", return_value=True):
        with pytest.raises(SystemExit) as ei:
            cli.cmd_start(args)
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "already running" in out.lower()
    assert "systemctl status devos" in out


def test_cmd_start_proceeds_when_port_free():
    args = SimpleNamespace(port=19000, host="127.0.0.1", dev=False, quiet=True)
    settings = MagicMock(WEB_CONCURRENCY=1)
    fake_config = MagicMock(settings=settings)
    fake_uv = MagicMock()
    with patch.object(cli, "port_is_in_use", return_value=False):
        with patch.dict(sys.modules, {"uvicorn": fake_uv, "core.config": fake_config}):
            cli.cmd_start(args)
    fake_uv.run.assert_called_once()
    kwargs = fake_uv.run.call_args.kwargs
    assert kwargs["port"] == 19000
    assert kwargs["host"] == "127.0.0.1"


def test_resolve_bind_custom_port_and_env(monkeypatch):
    monkeypatch.delenv("DEVOS_PORT", raising=False)
    monkeypatch.delenv("DEVOS_HOST", raising=False)
    args = SimpleNamespace(port=9000, host="127.0.0.1")
    assert cli._resolve_bind(args) == ("127.0.0.1", 9000)
    monkeypatch.setenv("DEVOS_PORT", "8123")
    monkeypatch.setenv("DEVOS_HOST", "127.0.0.1")
    args2 = SimpleNamespace(port=None, host=None)
    h, p = cli._resolve_bind(args2)
    assert p == 8123
    assert h == "127.0.0.1"
