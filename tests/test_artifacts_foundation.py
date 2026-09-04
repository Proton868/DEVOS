"""Phase 1 — artifact upload, zip-slip, export, app detect."""
import io
import zipfile
import tarfile

import pytest

from execution.files import FileService
from execution.artifacts import (
    extract_archive,
    export_project_zip,
    write_bytes,
    ArtifactError,
    content_hash,
)
from execution.app_detect import detect_application

USER = "artifact-user"
WS = "artifact-ws"


@pytest.fixture
def fs(tmp_path=None):
    import uuid
    return FileService(USER, WS + "-" + uuid.uuid4().hex[:8])


def test_write_and_hash(fs):
    meta = write_bytes(fs, "hello.txt", b"hello world")
    assert meta.size == 11
    assert meta.hash == content_hash(b"hello world")
    assert meta.path == "hello.txt"


def test_zip_slip_rejected(fs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "pwn")
        zf.writestr("/abs.txt", "pwn")
    with pytest.raises(ArtifactError) as ei:
        extract_archive(fs, "evil.zip", buf.getvalue())
    assert ei.value.code == "ARCHIVE_UNSAFE"


def test_zip_ok(fs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("src/app.js", "console.log(1)")
        zf.writestr("index.html", "<html></html>")
    metas = extract_archive(fs, "ok.zip", buf.getvalue())
    assert len(metas) == 2
    assert any(m.path == "src/app.js" for m in metas)


def test_tar_symlink_rejected(fs):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../x"
        tf.addfile(info)
    with pytest.raises(ArtifactError):
        extract_archive(fs, "evil.tar", buf.getvalue())


def test_export_excludes_secrets(fs):
    write_bytes(fs, "index.html", b"<html>ok</html>")
    write_bytes(fs, ".env", b"SECRET=1")
    blob = export_project_zip(fs, exclude_secrets=True)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    assert "index.html" in names
    assert ".env" not in names


def test_detect_static(fs):
    write_bytes(fs, "index.html", b"<html><body>Hi</body></html>")
    d = detect_application(fs)
    assert d["kind"] == "STATIC_SITE"


def test_detect_nextjs(fs):
    write_bytes(
        fs,
        "package.json",
        b'{"name":"x","dependencies":{"next":"14.0.0","react":"18.0.0"},"scripts":{"dev":"next dev","build":"next build"}}',
    )
    write_bytes(fs, "next.config.mjs", b"export default {}")
    d = detect_application(fs)
    assert d["kind"] == "NEXTJS_APP"
    assert d["package_manager"] in ("npm", "pnpm", "yarn", "bun")
