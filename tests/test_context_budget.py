from brain.context_budget import (
    budget_for, compact_tool_result, enforce_input_budget, approx_tokens, truncate_text,
)


def test_budget_classes_distinct():
    assert budget_for("routing").max_input_tokens < budget_for("coding").max_input_tokens
    assert budget_for("verify").max_input_tokens < budget_for("debug").max_input_tokens


def test_compact_tool_result_bounds():
    b = budget_for("coding")
    huge = {"ok": True, "content": "x" * 50_000, "stdout": "y" * 50_000}
    out = compact_tool_result(huge, b)
    assert len(out) <= b.max_tool_result_chars + 20


def test_enforce_history_trim():
    b = budget_for("simple")
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(30):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" * 20})
    out = enforce_input_budget(msgs, b)
    assert len(out) <= b.max_history_messages + 2
    assert out[0]["role"] == "system"


def test_truncate():
    assert "truncated" in truncate_text("abcdef", 5) or len(truncate_text("abcdef", 5)) <= 5 + 20
