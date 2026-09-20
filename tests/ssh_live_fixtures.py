"""Disposable local SSH server for live integration tests (asyncssh)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pytest

try:
    import asyncssh
    HAS_ASYNCSSH = True
except ImportError:
    HAS_ASYNCSSH = False


@dataclass
class LiveSshServerInfo:
    host: str
    port: int
    username: str
    password: str
    client_key_path: Path
    client_key_pem: str
    host_key_path: Path
    host_fingerprint: str
    host_key_type: str
    server: Any
    tmp: Path


def _fingerprint_of_key(key) -> str:
    try:
        # Prefer asyncssh helper if present
        pub = key.convert_to_public() if hasattr(key, "convert_to_public") else key
        if hasattr(asyncssh, "get_fingerprint"):
            fp = asyncssh.get_fingerprint(pub)
            if isinstance(fp, str):
                return fp if fp.startswith("SHA256:") else f"SHA256:{fp}"
    except Exception:
        pass
    raw = key.export_public_key("openssh")
    if isinstance(raw, str):
        raw_b = raw.encode()
    else:
        raw_b = raw
    digest = hashlib.sha256(raw_b).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


if HAS_ASYNCSSH:

    class _TestSSHServer(asyncssh.SSHServer):
        def connection_made(self, conn):
            self._conn = conn

        def begin_auth(self, username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return username == "testuser" and password == "testpass"

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key) -> bool:
            return username == "testuser"

    async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
        cmd = process.command
        try:
            if cmd is None:
                process.stdout.write("live-shell-ready\n")
                process.exit(0)
                return
            c = str(cmd).strip()
            if c in ("uname -a", "uname"):
                process.stdout.write("Linux live-test 6.1.0 #1 SMP x86_64 GNU/Linux\n")
                process.exit(0)
            elif c == "echo hello":
                process.stdout.write("hello\n")
                process.exit(0)
            elif c == "false":
                process.stderr.write("error\n")
                process.exit(1)
            elif c.startswith("sleep "):
                try:
                    secs = float(c.split()[1])
                    await asyncio.sleep(min(secs, 1.5))
                except Exception:
                    pass
                process.stdout.write("slept\n")
                process.exit(0)
            elif "SECRET_MARKER" in c:
                process.stdout.write("PASSWORD=supersecret_live\n")
                process.exit(0)
            else:
                process.stdout.write(f"ok:{c}\n")
                process.exit(0)
        except Exception:
            try:
                process.exit(1)
            except Exception:
                pass


async def start_live_ssh_server() -> LiveSshServerInfo:
    if not HAS_ASYNCSSH:
        raise RuntimeError("asyncssh_required")

    tmp = Path(tempfile.mkdtemp(prefix="devos-ssh-live-"))
    host_key_path = tmp / "host_key"
    client_key_path = tmp / "client_key"

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key.write_private_key(str(host_key_path))
    host_fingerprint = _fingerprint_of_key(host_key)
    host_key_type = "ssh-ed25519"

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key.write_private_key(str(client_key_path))
    client_key_pem = client_key.export_private_key("openssh").decode() if isinstance(
        client_key.export_private_key("openssh"), bytes
    ) else client_key.export_private_key("openssh")

    def server_factory():
        return _TestSSHServer()

    server = await asyncssh.create_server(
        server_factory,
        "127.0.0.1",
        0,
        server_host_keys=[str(host_key_path)],
        process_factory=_handle_client,
    )

    sockets = list(getattr(server, "sockets", None) or [])
    if not sockets and hasattr(server, "_server"):
        # some versions
        pass
    if not sockets:
        # asyncssh SSHAcceptor
        sock = getattr(server, "sockets", None)
        if sock:
            sockets = list(sock)
    port = None
    try:
        sockets = server.sockets  # type: ignore
        port = sockets[0].getsockname()[1]
    except Exception:
        # SSHAcceptor in asyncssh 2.x
        try:
            port = server.get_port()  # type: ignore
        except Exception:
            for s in getattr(server, "_extra", {}).values() if False else []:
                pass
            # last resort: internal _server
            srv = getattr(server, "_server", None)
            if srv:
                sockets = srv.sockets
                port = sockets[0].getsockname()[1]

    if port is None:
        await stop_live_ssh_server_obj(server)
        raise RuntimeError("failed_to_determine_listen_port")

    return LiveSshServerInfo(
        host="127.0.0.1",
        port=int(port),
        username="testuser",
        password="testpass",
        client_key_path=client_key_path,
        client_key_pem=client_key_pem,
        host_key_path=host_key_path,
        host_fingerprint=host_fingerprint,
        host_key_type=host_key_type,
        server=server,
        tmp=tmp,
    )


async def stop_live_ssh_server_obj(server) -> None:
    try:
        server.close()
        await server.wait_closed()
    except Exception:
        pass


async def stop_live_ssh_server(info: LiveSshServerInfo) -> None:
    await stop_live_ssh_server_obj(info.server)


@pytest.fixture
async def live_ssh_server(monkeypatch):
    if not HAS_ASYNCSSH:
        pytest.skip("asyncssh not installed")
    monkeypatch.setenv("DEVOS_SSH_ALLOW_LOCALHOST", "1")
    info = await start_live_ssh_server()
    yield info
    await stop_live_ssh_server(info)
