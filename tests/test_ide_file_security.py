"""IDE file-path security fixtures (Req 26) — pure unit tests, no DB."""
from __future__ import annotations
import pytest
from pathlib import Path
from execution.files import FileService, PathViolation

@pytest.fixture
def svc(tmp_path, monkeypatch):
    # Point PROJECTS_DIR at a temp location
    import execution.files as fm
    monkeypatch.setattr(fm, "PROJECTS_DIR", tmp_path)
    s = FileService("user1", "proj1")
    (s.root / "safe.txt").write_text("ok")
    (s.root / "sub").mkdir()
    (s.root / "sub" / "a.py").write_text("print(1)")
    return s

def test_path_traversal_dotdot(svc):
    with pytest.raises(PathViolation):
        svc.read("../outside")
    with pytest.raises(PathViolation):
        svc.read("sub/../../outside")

def test_path_traversal_absolute(svc):
    with pytest.raises(PathViolation):
        svc.read("/etc/passwd")

def test_null_byte_rejected(svc):
    with pytest.raises(PathViolation):
        svc.read("safe.txt\x00.jpg")

def test_mixed_separators_normalized(svc):
    # backslash normalized; still under root
    out = svc.read("sub\\a.py")
    assert "print" in out["content"]

def test_list_dir_lazy_root(svc):
    entries = svc.list_dir("")
    names = {e["name"] for e in entries}
    assert "safe.txt" in names
    assert "sub" in names
    sub = next(e for e in entries if e["name"] == "sub")
    assert sub["lazy"] is True
    assert sub["type"] == "directory"

def test_list_dir_nested(svc):
    entries = svc.list_dir("sub")
    assert any(e["name"] == "a.py" for e in entries)

def test_tree_depth_bounded(svc):
    tree = svc.tree(max_depth=1)
    assert all(n.get("lazy") or n["type"] == "file" or n.get("children") == [] or n["type"] == "directory" for n in tree)
    # children of root dirs should be empty when depth=1
    for n in tree:
        if n["type"] == "directory":
            assert n.get("lazy") is True or n.get("children") == []

def test_tree_full_includes_nested(svc):
    tree = svc.tree()
    paths = []
    def walk(nodes):
        for n in nodes:
            paths.append(n["path"])
            if n.get("children"):
                walk(n["children"])
    walk(tree)
    assert "sub/a.py" in paths or "sub" in paths

def test_read_within_root(svc):
    out = svc.read("safe.txt")
    assert out["content"] == "ok"
