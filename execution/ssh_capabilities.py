"""SSH capability interfaces — abstract over transport; no direct library use in business logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from execution.ssh_transport import (
    SshConnectParams,
    SshExecResult,
    SshTransportService,
    TransportSession,
)
from governance.ssh_host_verify import (
    verify_host_key,
    assert_connect_allowed,
    HostVerificationResult,
)
from governance.ssh_credentials import resolve_ssh_material


@dataclass
class SSHConnectionCapability:
    """Authorize and open a verified SSH connection for an owner."""

    owner_id: str
    transport: SshTransportService

    async def connect(
        self,
        *,
        connection_id: str,
        actor: str = "user",
        auto_approve_new: bool = False,
    ) -> tuple[TransportSession, HostVerificationResult]:
        from governance.ssh_domain import assert_connection_usable
        from governance.ssh_credentials import resolve_ssh_material

        row, host = await assert_connection_usable(self.owner_id, connection_id)
        params = SshConnectParams(
            host=host.hostname,
            port=int(host.port or 22),
            username=row.username,
        )

        material = None
        if row.credential_ref_id and row.auth_method != "agent_forwarding":
            material = await resolve_ssh_material(
                owner_id=self.owner_id,
                credential_ref_id=row.credential_ref_id,
                purpose="ssh_connect",
            )

        async def _verify(p: SshConnectParams):
            result = await verify_host_key(
                owner_id=self.owner_id,
                host_identity_id=row.host_identity_id,
                hostname=p.host,
                port=p.port,
                presented_key_type=p.host_key_type,
                presented_fingerprint=p.host_key_fingerprint,
                actor=actor,
                auto_approve_new=auto_approve_new,
            )
            assert_connect_allowed(result, actor=actor)
            p._verify_result = result  # type: ignore[attr-defined]
            return result

        try:
            session = await self.transport.connect_verified(
                params,
                private_key_pem=material.private_key_pem if material else None,
                password=material.password if material else None,
                passphrase=material.passphrase if material else None,
                agent_forwarding=bool(row.agent_forwarding),
                host_verify=_verify,
            )
            result = getattr(params, "_verify_result", None)
            if result is None:
                result = await verify_host_key(
                    owner_id=self.owner_id,
                    host_identity_id=row.host_identity_id,
                    hostname=params.host,
                    port=params.port,
                    presented_key_type=params.host_key_type,
                    presented_fingerprint=params.host_key_fingerprint,
                    actor=actor,
                    auto_approve_new=auto_approve_new,
                )
            return session, result
        finally:
            if material is not None:
                material.clear()


@dataclass
class SSHExecCapability:
    owner_id: str
    transport: SshTransportService

    async def run(self, session_id: str, command: str, *, timeout_s: float = 60.0) -> SshExecResult:
        return await self.transport.exec(session_id, command, timeout_s=timeout_s)


@dataclass
class SSHSessionCapability:
    owner_id: str
    transport: SshTransportService

    async def open_pty(self, session_id: str, *, cols: int = 80, rows: int = 24):
        return await self.transport.open_pty(session_id, cols=cols, rows=rows)

    async def close(self, session_id: str):
        await self.transport.close(session_id)


@dataclass
class SSHFileTransferCapability:
    """Governed SFTP-shaped transfer via governance.ssh_file_transfer."""

    owner_id: str

    async def transfer(self, **kwargs):
        from governance.ssh_file_transfer import governed_transfer, TransferRequest, TransferOp
        op = kwargs.get("op")
        if isinstance(op, str):
            op = TransferOp(op)
        req = TransferRequest(
            owner_id=self.owner_id,
            connection_id=kwargs["connection_id"],
            op=op,
            remote_path=kwargs.get("remote_path") or "/",
            local_relpath=kwargs.get("local_relpath"),
            project_id=kwargs.get("project_id"),
            remote_dest=kwargs.get("remote_dest"),
            overwrite=bool(kwargs.get("overwrite", False)),
            user_confirmed=bool(kwargs.get("user_confirmed", False)),
            max_bytes=int(kwargs.get("max_bytes", 50 * 1024 * 1024)),
            actor=kwargs.get("actor") or "user",
        )
        return await governed_transfer(req)
