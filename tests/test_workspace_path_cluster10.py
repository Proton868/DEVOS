"""Cluster 10: workspace / artifact path boundary security."""
from __future__ import annotations

import io
import zipfile

import pytest

from execution.files import FileService, PathViolation, PROJECTS_DIR
from execution.artifacts import (
    ArtifactError,
    extract_zip,
    _safe_dest_prefix,
    is_secret_path,
)


@pytest.fixture
def projects(tmp_path, monkeypatch):
    import execution.files as fm
    monkeypatch.setattr(fm, "PROJECTS_DIR", tmp_path)
    return tmp_path


def test_project_id_traversal_rejected(projects):
    with pytest.raises(PathViolation):
        FileService("alice", "../bob")
    with pytest.raises(PathViolation):
        FileService("alice", "foo/../bob")
    with pytest.raises(PathViolation):
        FileService("alice", "foo/bar")
    with pytest.raises(PathViolation):
        FileService("alice/evil", "proj")


def test_user_cannot_open_other_user_via_project_id(projects):
    a = FileService("alice", "site")
    a.write("index.html", "<html>a</html>")
    b = FileService("bob", "site")
    b.write("index.html", "<html>b</html>")
    # Attempt to point Alice service at Bob via traversal — must fail at construct
    with pytest.raises(PathViolation):
        FileService("alice", "../bob/site")
    assert a.read("index.html")["content"] == "<html>a</html>"
    assert b.read("index.html")["content"] == "<html>b</html>"


def test_path_dotdot_still_rejected(projects):
    svc = FileService("u1", "p1")
    svc.write("ok.txt", "x")
    with pytest.raises(PathViolation):
        svc.read("../p1/ok.txt")
    with pytest.raises(PathViolation):
        svc.write("../../etc/passwd", "nope")


def test_symlink_escape_rejected(projects):
    svc = FileService("u1", "p1")
    outside = projects / "outside.txt"
    outside.write_text("secret")
    link = svc.root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unsupported")
    with pytest.raises(PathViolation):
        svc.read("link.txt")


def test_secret_path_write_refused(projects):
    svc = FileService("u1", "p1")
    with pytest.raises(PathViolation):
        svc.write(".env", "SECRET=1")
    with pytest.raises(PathViolation):
        svc.write_bytes("id_rsa", b"key")


def test_dest_prefix_traversal_refused():
    with pytest.raises(ArtifactError):
        _safe_dest_prefix("../escape")
    assert _safe_dest_prefix("uploads/x") == "uploads/x"


def test_zip_slip_member_refused(projects):
    svc = FileService("u1", "p1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "bad")
    with pytest.raises(ArtifactError):
        extract_zip(svc, buf.getvalue())


def test_is_secret_path_helpers():
    assert is_secret_path(".env")
    assert is_secret_path("keys/id_rsa")
    assert not is_secret_path("src/main.py")
