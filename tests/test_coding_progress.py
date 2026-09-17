from brain.coding_progress import build_coding_progress, merge_progress


def test_build_never_implies_success():
    p = build_coding_progress(
        mission_id="m1",
        status="agent_progress",
        command="pytest -q",
        command_exit_code=0,
        command_ok=True,
        files_changed=["a.py"],
        provider="omniroute",
        model="free",
    )
    assert p["success_implied"] is False
    assert p["mission_id"] == "m1"
    assert p["command"] == "pytest -q"
    assert p["files_changed"] == ["a.py"]


def test_failed_status_visible():
    p = build_coding_progress(status="failed", error="boom", mission_id="m")
    assert p["status"] == "failed"
    assert p["error"] == "boom"
    assert p["success_implied"] is False


def test_scrub_secrets():
    p = build_coding_progress(command_stdout_tail="token ghp_ABCDEF secret")
    assert "ghp_" not in (p.get("command_stdout_tail") or "")


def test_merge_accumulates_files():
    a = build_coding_progress(files_changed=["a.py"], status="agent_progress")
    b = build_coding_progress(files_changed=["b.py"], command="pytest")
    m = merge_progress(a, b)
    assert "a.py" in m["files_changed"] and "b.py" in m["files_changed"]
    assert m["command"] == "pytest"
    assert m["success_implied"] is False


def test_acceptance_requires_reason_for_ok():
    p = build_coding_progress(acceptance={"ok": True})
    assert p["acceptance"]["ok"] is False
