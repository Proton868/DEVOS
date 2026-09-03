"""Focused tests for Agentic IDE tool registry, patches, and mode filters."""
from __future__ import annotations

import pytest

from brain.agent_tools import (
    AGENT_TOOL_REGISTRY,
    AgentMode,
    apply_context_replace,
    apply_unified_diff,
    get_agent_tool,
    list_agent_tools,
    make_line_diff,
)


def test_registry_has_core_tools():
    required = {
        "list_files", "read_file", "search_files", "apply_patch",
        "create_file", "delete_file", "run_command", "run_tests",
        "git_status", "git_commit",
    }
    assert required.issubset(set(AGENT_TOOL_REGISTRY.keys()))


def test_no_destructive_git_tools():
    banned = {"git_reset_hard", "git_clean", "force_push", "git_push_force"}
    assert banned.isdisjoint(set(AGENT_TOOL_REGISTRY.keys()))


def test_tools_have_schemas():
    for name, tool in AGENT_TOOL_REGISTRY.items():
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.description, str) and tool.description
        ok, err = tool.validate_args({})
        # may fail required fields — that's fine; must not throw
        assert err is None or isinstance(err, str)


def test_invalid_args_rejected():
    tool = get_agent_tool("read_file")
    ok, err = tool.validate_args({})
    assert ok is False
    assert "path" in (err or "")
    ok, err = tool.validate_args({"path": 123})
    assert ok is False


def test_unknown_tool():
    assert get_agent_tool("totally_fake_tool") is None


def test_mode_filters():
    ask = {t["name"] for t in list_agent_tools(AgentMode.ASK)}
    edit = {t["name"] for t in list_agent_tools(AgentMode.EDIT)}
    agent = {t["name"] for t in list_agent_tools(AgentMode.AGENT)}
    assert "read_file" in ask
    assert "apply_patch" not in ask
    assert "apply_patch" in edit
    assert "run_tests" not in edit
    assert "run_tests" in agent
    assert "git_commit" in agent


def test_context_replace_patch():
    content = "hello world\nline2\n"
    ok, new, err = apply_context_replace(content, "world", "DEVOS")
    assert ok and err is None
    assert "hello DEVOS" in new
    ok, new, err = apply_context_replace(content, "missing", "x")
    assert not ok
    assert "patch_conflict" in (err or "")


def test_unified_diff_apply_and_conflict():
    content = "a\nb\nc\n"
    diff = """@@ -1,3 +1,3 @@
 a
-b
+B
 c
"""
    ok, new, err = apply_unified_diff(content, diff)
    assert ok, err
    assert new.splitlines() == ["a", "B", "c"]

    bad = """@@ -1,3 +1,3 @@
 a
-x
+y
 c
"""
    ok, _, err = apply_unified_diff(content, bad)
    assert not ok
    assert "patch_conflict" in (err or "")


def test_make_line_diff():
    d = make_line_diff("a\nb\n", "a\nc\n")
    types = {x["type"] for x in d}
    assert "add" in types or "del" in types


def test_parse_tool_call_roundtrip():
    from brain.agent_runtime import _parse_tool_call
    text = '{"thought":"inspect","action":"list_files","action_input":{"path":""}}'
    obj = _parse_tool_call(text)
    assert obj["action"] == "list_files"
    assert _parse_tool_call("no json here") is None
