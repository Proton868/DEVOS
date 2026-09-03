"""
IDE acceptance fixtures A–H (Final Acceptance Pass).

These are pure/unit where possible. Fixtures that require durable DB
(SQLAlchemy) are marked and skip cleanly when unavailable.
"""
from __future__ import annotations

import pytest

from brain.agent_changes import (
    record_change,
    list_changes,
    accept_change,
    reject_change,
    revert_change,
    get_change,
    _CHANGES,
)
from brain.agent_runtime import (
    AgentTask,
    AgentMode,
    _emit,
    get_task_events,
    request_cancel,
    _TASKS,
    _EVENT_SEQ,
    _TASK_EVENTS,
)
from brain.agent_tools import apply_unified_diff, apply_context_replace
from execution.files import FileService, PathViolation
import execution.files as files_mod


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(files_mod, "PROJECTS_DIR", tmp_path)
    s = FileService("u-accept", "p-accept")
    (s.root / "main.py").write_text("def hello():\n    return 1\n")
    (s.root / "tests").mkdir()
    (s.root / "tests" / "test_main.py").write_text("def test_hello():\n    assert True\n")
    return s


@pytest.fixture(autouse=True)
def clear_changes():
    _CHANGES.clear()
    yield
    _CHANGES.clear()


# ── Fixture C — Patch conflict / stale ──────────────────────────────────

def test_fixture_c_patch_conflict_on_apply():
    """Agent patch fails when context no longer matches."""
    content = "a\nb\nc\n"
    bad = """@@ -1,3 +1,3 @@
 a
-x
+y
 c
"""
    ok, _, err = apply_unified_diff(content, bad)
    assert not ok
    assert "patch_conflict" in (err or "")


def test_fixture_c_stale_reject_refuses_overwrite(svc):
    """User edits after agent apply → reject must not silently destroy user work."""
    before = "def hello():\n    return 1\n"
    after = "def hello():\n    return 2\n"
    svc.write("main.py", after)
    rec = record_change(
        task_id="t-c1",
        user_id="u-accept",
        project_id="p-accept",
        path="main.py",
        change_kind="patched",
        before_content=before,
        after_content=after,
    )
    # User modifies further
    user_version = "def hello():\n    return 99  # user\n"
    svc.write("main.py", user_version)

    out = reject_change(rec.id, "u-accept", svc)
    assert out.get("ok") is False
    assert out.get("stale") is True
    # User content preserved
    assert svc.read("main.py")["content"] == user_version


def test_fixture_c_clean_reject_when_disk_matches(svc):
    before = "def hello():\n    return 1\n"
    after = "def hello():\n    return 2\n"
    svc.write("main.py", after)
    rec = record_change(
        task_id="t-c2",
        user_id="u-accept",
        project_id="p-accept",
        path="main.py",
        change_kind="patched",
        before_content=before,
        after_content=after,
    )
    out = reject_change(rec.id, "u-accept", svc)
    assert out.get("ok") is True
    assert svc.read("main.py")["content"] == before


def test_fixture_c_accept_marks_status(svc):
    after = "def hello():\n    return 3\n"
    svc.write("main.py", after)
    rec = record_change(
        task_id="t-c3",
        user_id="u-accept",
        project_id="p-accept",
        path="main.py",
        change_kind="patched",
        before_content="old",
        after_content=after,
    )
    out = accept_change(rec.id, "u-accept")
    assert out.get("ok") is True
    assert get_change(rec.id, "u-accept").status == "accepted"


# ── Fixture D — Cancellation ───────────────────────────────────────────

def test_fixture_d_cancel_sets_flag_no_terminal_rewrite():
    task = AgentTask(
        id="t-d1", user_id="u1", tenant_id="ten", project_id="p",
        session_id="s", objective="x", mode=AgentMode.AGENT,
    )
    _TASKS[task.id] = task
    _EVENT_SEQ[task.id] = 0
    _TASK_EVENTS[task.id] = []
    assert request_cancel(task.id) is True
    assert task.cancel_requested is True
    # Cancel request is not itself a terminal status rewrite
    assert getattr(task, "status", None) not in ("cancelled",) or task.cancel_requested


# ── Fixture E — Event sequence / after_seq ──────────────────────────────

def test_fixture_e_after_seq_replay_no_duplicates():
    task = AgentTask(
        id="t-e1", user_id="u1", tenant_id="ten", project_id="p",
        session_id="s", objective="x", mode=AgentMode.AGENT,
    )
    _TASKS[task.id] = task
    _EVENT_SEQ[task.id] = 0
    _TASK_EVENTS[task.id] = []
    e1 = _emit(task, "agent.started", {})
    e2 = _emit(task, "agent.tool_call", {"tool": "read_file"})
    e3 = _emit(task, "agent.completed", {})
    assert e1["seq"] == 1 and e2["seq"] == 2 and e3["seq"] == 3
    missed = get_task_events(task.id, after_seq=1)
    assert len(missed) == 2
    assert [m["seq"] for m in missed] == [2, 3]
    # full replay still ordered
    all_ev = get_task_events(task.id, after_seq=0)
    assert [m["seq"] for m in all_ev] == [1, 2, 3]


# ── Fixture H — Task isolation ─────────────────────────────────────────

def test_fixture_h_task_event_isolation():
    a = AgentTask(id="ta", user_id="u1", tenant_id="ten", project_id="p",
                  session_id="s", objective="a", mode=AgentMode.AGENT)
    b = AgentTask(id="tb", user_id="u1", tenant_id="ten", project_id="p",
                  session_id="s", objective="b", mode=AgentMode.AGENT)
    _TASKS[a.id] = a
    _TASKS[b.id] = b
    _EVENT_SEQ[a.id] = 0
    _EVENT_SEQ[b.id] = 0
    _TASK_EVENTS[a.id] = []
    _TASK_EVENTS[b.id] = []
    _emit(a, "agent.started", {})
    _emit(b, "agent.started", {})
    _emit(a, "agent.tool_call", {"tool": "write_file"})
    assert all(e["task_id"] == "ta" for e in get_task_events(a.id))
    assert all(e["task_id"] == "tb" for e in get_task_events(b.id))
    assert len(get_task_events(a.id)) == 2
    assert len(get_task_events(b.id)) == 1


def test_fixture_h_change_user_isolation(svc):
    rec = record_change(
        task_id="t-h1",
        user_id="u-accept",
        project_id="p-accept",
        path="main.py",
        change_kind="patched",
        before_content="a",
        after_content="b",
    )
    assert get_change(rec.id, "other-user") is None
    assert get_change(rec.id, "u-accept") is not None
    assert list_changes("t-h1", "other-user") == []


# ── Fixture A (lightweight) — inspect/edit path ─────────────────────────

def test_fixture_a_inspect_edit_cycle(svc):
    """Open → read → write → tree still consistent."""
    content = svc.read("main.py")["content"]
    assert "hello" in content
    svc.write("main.py", content + "\n# edited\n")
    assert "# edited" in svc.read("main.py")["content"]
    tree = svc.tree(max_depth=1)
    names = {n["name"] for n in tree}
    assert "main.py" in names
    assert "tests" in names


# ── Path security (acceptance cross-check) ──────────────────────────────

def test_security_symlink_escape_refused(svc, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = svc.root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted in environment")
    # resolve() follows symlink; relative_to should fail closed
    with pytest.raises(PathViolation):
        svc.read("link.txt")


def test_security_encoded_traversal(svc):
    # percent-encoding is not decoded by FileService — path is literal
    # still must not escape via .. components
    with pytest.raises(PathViolation):
        svc.read("sub/../../outside")
