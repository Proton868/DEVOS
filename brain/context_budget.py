"""Bounded LLM context construction by task class.

Does not hide failures — preserves evidence-critical content while
avoiding resending entire workspaces or unbounded tool dumps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Approximate chars per token (conservative for mixed code)
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ContextBudget:
    """Hard ceilings for a single model call (not a mission lifetime)."""
    name: str
    max_input_tokens: int
    max_tool_result_chars: int
    max_history_messages: int
    max_file_excerpt_chars: int
    max_system_chars: int


BUDGETS: dict[str, ContextBudget] = {
    "routing": ContextBudget("routing", 2_000, 1_000, 4, 800, 1_500),
    "planning": ContextBudget("planning", 8_000, 2_000, 8, 2_000, 4_000),
    "simple": ContextBudget("simple", 4_000, 2_000, 6, 1_500, 3_000),
    "coding": ContextBudget("coding", 16_000, 6_000, 12, 4_000, 6_000),
    "debug": ContextBudget("debug", 20_000, 8_000, 16, 6_000, 6_000),
    "verify": ContextBudget("verify", 6_000, 3_000, 8, 3_000, 3_000),
}


def budget_for(purpose: str | None) -> ContextBudget:
    p = (purpose or "coding").strip().lower()
    aliases = {
        "classification": "routing",
        "classify": "routing",
        "plan": "planning",
        "execution": "simple",
        "execute": "simple",
        "code": "coding",
        "reasoning": "debug",
        "debugging": "debug",
        "complex": "debug",
        "verification": "verify",
        "final": "verify",
    }
    return BUDGETS.get(aliases.get(p, p), BUDGETS["coding"])


def approx_tokens(text: str) -> int:
    return max(0, (len(text or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def truncate_text(text: str, max_chars: int, *, suffix: str = "\n…[truncated]") -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix


def compact_tool_result(payload: dict | str, budget: ContextBudget) -> str:
    """Serialize tool result for the model under char budget."""
    if isinstance(payload, str):
        return truncate_text(payload, budget.max_tool_result_chars)
    import json
    try:
        # Prefer structured small fields
        slim = {}
        for k in ("ok", "path", "error", "status", "exit_code", "summary", "lines", "count"):
            if k in payload:
                slim[k] = payload[k]
        content = payload.get("content") or payload.get("stdout") or payload.get("output")
        if content is not None:
            slim["content"] = truncate_text(str(content), max(200, budget.max_tool_result_chars // 2))
        if payload.get("stderr"):
            slim["stderr"] = truncate_text(str(payload["stderr"]), 800)
        raw = json.dumps(slim if slim else payload, default=str)
    except Exception:
        raw = str(payload)
    return truncate_text(raw, budget.max_tool_result_chars)


def trim_message_history(messages: list[dict], budget: ContextBudget) -> list[dict]:
    """Keep system + first user + recent tail within message count budget."""
    if len(messages) <= budget.max_history_messages:
        return messages
    system = [m for m in messages if m.get("role") == "system"][:1]
    rest = [m for m in messages if m.get("role") != "system"]
    if not rest:
        return system
    first = rest[:1]
    tail_n = max(1, budget.max_history_messages - len(system) - 1)
    tail = rest[-tail_n:]
    # Avoid duplicate if first is in tail
    if first and tail and first[0] is tail[0]:
        return system + tail
    merged = system + first
    for m in tail:
        if m not in merged:
            merged.append(m)
    return merged


def enforce_input_budget(messages: list[dict], budget: ContextBudget) -> list[dict]:
    """Soft-trim message contents if total approx tokens exceed budget."""
    messages = trim_message_history(messages, budget)
    total = sum(approx_tokens(str(m.get("content") or "")) for m in messages)
    if total <= budget.max_input_tokens:
        return messages
    # Shrink non-system contents proportionally
    out = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "system":
            content = truncate_text(content, budget.max_system_chars)
        else:
            content = truncate_text(content, max(500, budget.max_input_tokens * _CHARS_PER_TOKEN // max(1, len(messages))))
        out.append({**m, "content": content})
    return out
