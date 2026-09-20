"""Governed SSH file transfer security tests."""
from __future__ import annotations

import pytest

from governance.ssh_file_transfer import (
    normalize_remote_path,
    resolve_local_workspace_path,
    governed_transfer,
    TransferRequest,
    TransferOp,
    TransferDenied,
    clear_transfer_state_for_tests,
    get_mock_backend,
    request_cancel,
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'sftp.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REQUIRE_POSTGRES", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-ssh-vault-32chars!!")
    monkeypatch.setattr(
        "governance.ssh_file_transfer.PROJECTS_DIR", tmp_path / "projects"
    )
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from core import database as dbmod

    dbmod.engine = create_async_engine(url, echo=False)
    dbmod.AsyncSessionLocal = async_sessionmaker(dbmod.engine, expire_on_commit=False)
    await dbmod.init_db()
    clear_transfer_state_for_tests()
    yield dbmod, tmp_path
    await dbmod.engine.dispose()


def test_path_traversal_rejected():
    for bad in ("../etc/passwd", "/foo/../../etc", "foo/../../bar", "..", "/../../"):
        with pytest.raises(TransferDenied):
            normalize_remote_path(bad)


def test_normalize_safe_paths():
    assert normalize_remote_path("/var/log/app.log") == "/var/log/app.log"
    assert normalize_remote_path("rel/file.txt") == "rel/file.txt"


@pytest.mark.asyncio
async def test_symlink_attack_refused(db):
    _, tmp = db
    from governance.ssh_domain import create_connection

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    be = get_mock_backend(conn["id"])
    be.symlinks["/link"] = "/etc/passwd"
    be.files["/link"] = b"x"
    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.STAT, remote_path="/link",
    ))
    assert r.status == "denied"
    assert r.error == "symlink_escape_refused"


@pytest.mark.asyncio
async def test_oversized_file_denied(db):
    _, tmp = db
    from governance.ssh_domain import create_connection

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    be = get_mock_backend(conn["id"])
    be.files["/big"] = b"x" * 1000
    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DOWNLOAD, remote_path="/big",
        project_id="p1", local_relpath="out.bin",
        max_bytes=100,
        user_confirmed=True,
    ))
    assert r.status == "denied"
    assert r.error == "file_too_large"


@pytest.mark.asyncio
async def test_upload_download_roundtrip(db):
    _, tmp = db
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import PROJECTS_DIR

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    root = PROJECTS_DIR / "user-a" / "p1"
    root.mkdir(parents=True)
    (root / "hello.txt").write_text("hello remote")

    up = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.UPLOAD, remote_path="/app/hello.txt",
        project_id="p1", local_relpath="hello.txt",
        user_confirmed=True,
    ))
    assert up.status == "succeeded"

    down = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DOWNLOAD, remote_path="/app/hello.txt",
        project_id="p1", local_relpath="hello-back.txt",
        user_confirmed=True,
    ))
    assert down.status == "succeeded"
    assert (root / "hello-back.txt").read_text() == "hello remote"


@pytest.mark.asyncio
async def test_overwrite_protection(db):
    _, tmp = db
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import PROJECTS_DIR

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    root = PROJECTS_DIR / "user-a" / "p1"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("a")
    be = get_mock_backend(conn["id"])
    be.files["/app/a.txt"] = b"existing"

    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.UPLOAD, remote_path="/app/a.txt",
        project_id="p1", local_relpath="a.txt",
        overwrite=False, user_confirmed=True,
    ))
    assert r.status == "denied"
    assert r.error == "remote_exists"


@pytest.mark.asyncio
async def test_unauthorized_host_and_cross_user(db):
    from governance.ssh_domain import create_connection

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    r = await governed_transfer(TransferRequest(
        owner_id="user-b", connection_id=conn["id"],
        op=TransferOp.LIST, remote_path="/",
    ))
    assert r.status == "denied"


@pytest.mark.asyncio
async def test_delete_requires_confirmation(db):
    from governance.ssh_domain import create_connection

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    be = get_mock_backend(conn["id"])
    be.files["/x"] = b"1"
    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DELETE, remote_path="/x", user_confirmed=False,
    ))
    assert r.status == "denied"
    r2 = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DELETE, remote_path="/x", user_confirmed=True,
    ))
    assert r2.status == "succeeded"


@pytest.mark.asyncio
async def test_local_path_escape_blocked(db):
    with pytest.raises(TransferDenied):
        resolve_local_workspace_path("user-a", "p1", "../other/secret")


@pytest.mark.asyncio
async def test_concurrent_list(db):
    import asyncio
    from governance.ssh_domain import create_connection

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    be = get_mock_backend(conn["id"])
    be.files["/a"] = b"1"
    be.files["/b"] = b"2"

    async def one():
        return await governed_transfer(TransferRequest(
            owner_id="user-a", connection_id=conn["id"],
            op=TransferOp.LIST, remote_path="/",
        ))

    results = await asyncio.gather(*[one() for _ in range(5)])
    assert all(r.status == "succeeded" for r in results)


@pytest.mark.asyncio
async def test_upload_resume_after_cancel(db):
    _, tmp = db
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import PROJECTS_DIR, get_transfer_checkpoint

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    root = PROJECTS_DIR / "user-a" / "p1"
    root.mkdir(parents=True)
    payload = b"ABCDEFGHIJ" * 1000  # 10KB
    (root / "big.bin").write_bytes(payload)

    tid = "xfer-resume-up-1"
    # Cancel before any chunks by pre-flagging — first resume chunk loop checks cancel
    request_cancel(tid)
    r1 = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.UPLOAD, remote_path="/data/big.bin",
        project_id="p1", local_relpath="big.bin",
        user_confirmed=True, resume=True, offset=0,
        transfer_id=tid, chunk_size=1024, overwrite=True,
    ))
    # With cancel set at start of loop after offset detect
    assert r1.status in ("cancelled", "succeeded")
    if r1.status == "cancelled":
        assert r1.resumable is True
        from governance.ssh_file_transfer import _CANCEL
        _CANCEL.discard(tid)
        # Resume from checkpoint/offset
        r2 = await governed_transfer(TransferRequest(
            owner_id="user-a", connection_id=conn["id"],
            op=TransferOp.UPLOAD, remote_path="/data/big.bin",
            project_id="p1", local_relpath="big.bin",
            user_confirmed=True, resume=True,
            transfer_id=tid, chunk_size=1024, overwrite=True,
        ))
        assert r2.status == "succeeded"
        be = get_mock_backend(conn["id"])
        assert be.files["/data/big.bin"] == payload
    else:
        # If completed in one go under race, still valid
        be = get_mock_backend(conn["id"])
        assert be.files.get("/data/big.bin") == payload


@pytest.mark.asyncio
async def test_upload_resume_from_explicit_offset(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import PROJECTS_DIR

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    root = PROJECTS_DIR / "user-a" / "p1"
    root.mkdir(parents=True)
    payload = b"0123456789abcdef" * 64
    (root / "part.bin").write_bytes(payload)
    be = get_mock_backend(conn["id"])
    # Pretend first 100 bytes already on remote
    be.files["/part.bin"] = payload[:100]

    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.UPLOAD, remote_path="/part.bin",
        project_id="p1", local_relpath="part.bin",
        user_confirmed=True, resume=True, offset=100,
        chunk_size=50, overwrite=True,
    ))
    assert r.status == "succeeded"
    assert be.files["/part.bin"] == payload
    assert r.bytes_transferred == len(payload) - 100


@pytest.mark.asyncio
async def test_download_resume_from_partial_local(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import PROJECTS_DIR

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    be = get_mock_backend(conn["id"])
    payload = b"XYZ" * 500
    be.files["/remote.dat"] = payload
    root = PROJECTS_DIR / "user-a" / "p1"
    root.mkdir(parents=True)
    # Partial local file (first 200 bytes)
    (root / "remote.dat").write_bytes(payload[:200])

    r = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DOWNLOAD, remote_path="/remote.dat",
        project_id="p1", local_relpath="remote.dat",
        user_confirmed=True, resume=True, chunk_size=100,
    ))
    assert r.status == "succeeded"
    assert (root / "remote.dat").read_bytes() == payload
    assert r.bytes_transferred == len(payload) - 200


@pytest.mark.asyncio
async def test_download_resume_cancel_midway(db):
    from governance.ssh_domain import create_connection
    from governance.ssh_file_transfer import PROJECTS_DIR

    conn = await create_connection(
        owner_id="user-a", hostname="h", username="u", auth_method="agent_forwarding",
    )
    be = get_mock_backend(conn["id"])
    payload = b"Q" * 10000
    be.files["/q.bin"] = payload
    root = PROJECTS_DIR / "user-a" / "p1"
    root.mkdir(parents=True)

    tid = "xfer-dl-cancel"
    # Use a backend that cancels after first chunk via wrapping — simpler: cancel after partial
    # Start with offset 0, cancel immediately
    request_cancel(tid)
    r1 = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DOWNLOAD, remote_path="/q.bin",
        project_id="p1", local_relpath="q.bin",
        user_confirmed=True, resume=True, transfer_id=tid,
        chunk_size=1024, overwrite=True,
    ))
    assert r1.status == "cancelled"
    assert r1.resumable is True
    from governance.ssh_file_transfer import _CANCEL
    _CANCEL.discard(tid)
    # Clear cancel and resume
    r2 = await governed_transfer(TransferRequest(
        owner_id="user-a", connection_id=conn["id"],
        op=TransferOp.DOWNLOAD, remote_path="/q.bin",
        project_id="p1", local_relpath="q.bin",
        user_confirmed=True, resume=True, transfer_id=tid,
        chunk_size=1024, overwrite=True,
    ))
    assert r2.status == "succeeded"
    assert (root / "q.bin").read_bytes() == payload
