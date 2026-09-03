"""Unit tests for LSP manager path isolation and server discovery."""
from __future__ import annotations

from pathlib import Path

from execution.lsp_manager import (
    SUPPORTED_LANGUAGES,
    list_available_servers,
    project_root,
    real_to_virtual,
    rewrite_uris_in_obj,
    virtual_to_real,
)


def test_supported_languages_include_core_set():
    for lang in ("python", "typescript", "javascript", "json", "yaml", "html", "css"):
        assert lang in SUPPORTED_LANGUAGES


def test_virtual_to_real_confined(tmp_path, monkeypatch):
    import execution.lsp_manager as m
    monkeypatch.setattr(m, "PROJECTS_DIR", tmp_path)
    root = project_root("user1", "proj1")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x=1\n")

    ok = virtual_to_real("file:///workspace/src/a.py", root)
    assert ok is not None
    assert ok == (root / "src" / "a.py").resolve()

    # traversal
    assert virtual_to_real("file:///workspace/../../etc/passwd", root) is None
    assert virtual_to_real("file:///etc/passwd", root) is None


def test_real_to_virtual_roundtrip(tmp_path, monkeypatch):
    import execution.lsp_manager as m
    monkeypatch.setattr(m, "PROJECTS_DIR", tmp_path)
    root = project_root("u", "p")
    path = root / "pkg" / "mod.py"
    path.parent.mkdir(parents=True)
    path.write_text("")
    uri = real_to_virtual(path, root)
    assert uri == "file:///workspace/pkg/mod.py"
    assert virtual_to_real(uri, root) == path.resolve()


def test_rewrite_uris():
    root = Path("/tmp/fake-root-does-not-matter")
    # rewrite only applies via transform callback
    def upper_uri(u: str):
        return u + "#x" if u.startswith("file://") else u

    obj = {
        "textDocument": {"uri": "file:///workspace/a.py"},
        "other": 1,
        "nested": [{"uri": "file:///workspace/b.py"}],
    }
    out = rewrite_uris_in_obj(obj, upper_uri)
    assert out["textDocument"]["uri"].endswith("#x")
    assert out["nested"][0]["uri"].endswith("#x")


def test_list_available_servers_shape():
    servers = list_available_servers()
    assert "python" in servers
    assert "available" in servers["python"]
    assert "command" in servers["python"]
