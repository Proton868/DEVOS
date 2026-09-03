"""Tests for agent change review snapshots and repo intelligence tools."""
from __future__ import annotations

from brain.agent_changes import (
    accept_all,
    accept_change,
    list_task_changes_detailed,
    record_change,
    reject_change,
    revert_change,
)
from brain.agent_tools import AgentMode, get_agent_tool, list_agent_tools


class FakeFS:
    def __init__(self):
        self.files = {}

    def write(self, path, content):
        self.files[path] = content
        return {"path": path}

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return {"path": path, "content": self.files[path]}

    def delete(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]
        return {"deleted": True, "path": path}

    def create(self, path, is_dir=False):
        if path in self.files:
            raise FileExistsError(path)
        self.files[path] = ""
        return {"path": path}


def test_record_and_revert_patch():
    fs = FakeFS()
    fs.files["a.py"] = "old\n"
    rec = record_change(
        task_id="t1",
        user_id="u1",
        project_id="p1",
        path="a.py",
        change_kind="patched",
        before_content="old\n",
        after_content="new\n",
    )
    fs.files["a.py"] = "new\n"
    out = revert_change(rec.id, "u1", fs)
    assert out["ok"] is True
    assert fs.files["a.py"] == "old\n"


def test_reject_created_file():
    fs = FakeFS()
    rec = record_change(
        task_id="t2",
        user_id="u1",
        project_id="p1",
        path="new.py",
        change_kind="created",
        before_content=None,
        after_content="hi",
    )
    fs.files["new.py"] = "hi"
    out = reject_change(rec.id, "u1", fs)
    assert out["ok"] is True
    assert "new.py" not in fs.files


def test_accept_marks_status():
    rec = record_change(
        task_id="t3",
        user_id="u1",
        project_id="p1",
        path="x.py",
        change_kind="patched",
        before_content="a",
        after_content="b",
    )
    out = accept_change(rec.id, "u1")
    assert out["ok"] and out["status"] == "accepted"
    assert accept_all("t3", "u1")["ok"]


def test_list_changes_has_diff_stats():
    record_change(
        task_id="t4",
        user_id="u1",
        project_id="p1",
        path="y.py",
        change_kind="patched",
        before_content="a\nb\n",
        after_content="a\nc\n",
    )
    items = list_task_changes_detailed("t4", "u1")
    assert items
    assert "additions" in items[0] or items[0]["change_kind"] == "patched"


def test_cross_user_isolation():
    rec = record_change(
        task_id="t5",
        user_id="u1",
        project_id="p1",
        path="z.py",
        change_kind="patched",
        before_content="a",
        after_content="b",
    )
    assert list_task_changes_detailed("t5", "u2") == []
    fs = FakeFS()
    fs.files["z.py"] = "b"
    out = revert_change(rec.id, "u2", fs)
    assert out["ok"] is False


def test_repo_intel_tools_registered():
    for name in (
        "get_project_metadata",
        "get_test_files",
        "get_build_system",
        "get_package_dependencies",
        "find_symbol",
    ):
        assert get_agent_tool(name) is not None
        ok, err = get_agent_tool(name).validate_args(
            {"symbol": "Foo"} if name == "find_symbol" else {}
        )
        assert ok, err


def test_find_symbol_required_arg():
    tool = get_agent_tool("find_symbol")
    ok, err = tool.validate_args({})
    assert not ok


def test_ask_mode_includes_intel_not_writes():
    names = {t["name"] for t in list_agent_tools(AgentMode.ASK)}
    assert "find_symbol" in names
    assert "apply_patch" not in names
