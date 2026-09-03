"""
DEVOS Agentic IDE — Canonical Tool Registry.

Tools are metadata + execution handlers. Authorization is NOT decided here.
Every consequential tool routes through UCIP + require_authority() + Evidence
via the agent runtime.

LLM-generated tool arguments are untrusted input.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("devos.agent_tools")


class SideEffect(str, Enum):
    NONE = "none"
    LOCAL = "local"
    UNKNOWN = "unknown"


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentMode(str, Enum):
    """UX-level intent constraints layered on UCIP — not independent security."""
    ASK = "ask"         # read/search/explain
    EDIT = "edit"       # read + governed workspace modifications
    AGENT = "agent"     # full authorized development tools
    REVIEW = "review"   # read-only code review


# Tools permitted per mode (subset of registry). UCIP still enforces authority.
MODE_TOOLS: dict[AgentMode, set[str]] = {
    AgentMode.ASK: {
        "list_files", "read_file", "search_files", "get_file_metadata",
        "git_status", "git_diff", "git_log", "git_show", "git_branch",
        "get_job", "get_job_logs", "get_evidence",
        "list_workflows", "inspect_workflow",
        "get_project_metadata", "get_test_files", "get_build_system",
        "get_package_dependencies", "find_symbol",
    },
    AgentMode.EDIT: {
        "list_files", "read_file", "search_files", "get_file_metadata",
        "create_file", "apply_patch", "replace_text", "rename_file", "delete_file",
        "git_status", "git_diff", "git_log", "git_show", "git_branch",
        "get_project_metadata", "get_test_files", "get_build_system",
        "get_package_dependencies", "find_symbol",
    },
    AgentMode.AGENT: {
        "list_files", "read_file", "search_files", "get_file_metadata",
        "create_file", "apply_patch", "replace_text", "rename_file", "delete_file",
        "run_command", "run_tests", "run_build", "run_linter",
        "git_status", "git_diff", "git_log", "git_show", "git_branch",
        "git_add", "git_commit",
        "get_job", "get_job_logs", "get_evidence",
        "list_workflows", "inspect_workflow", "execute_workflow",
        "get_project_metadata", "get_test_files", "get_build_system",
        "get_package_dependencies", "find_symbol",
    },
    AgentMode.REVIEW: {
        "list_files", "read_file", "search_files", "get_file_metadata",
        "git_status", "git_diff", "git_log", "git_show", "git_branch",
        "get_evidence",
        "get_project_metadata", "get_test_files", "get_build_system",
        "get_package_dependencies", "find_symbol",
    },
}


@dataclass
class AgentTool:
    name: str
    description: str
    input_schema: dict
    capability: Optional[str]  # UCIP capability slug or None if always-allowed
    side_effect: SideEffect = SideEffect.NONE
    risk: ToolRisk = ToolRisk.LOW
    timeout_s: int = 30
    supports_streaming: bool = False
    supports_cancellation: bool = False
    # PathClass-ish: whether durable job/evidence is expected for this tool
    durable: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "capability": self.capability,
            "side_effect": self.side_effect.value,
            "risk": self.risk.value,
            "timeout_s": self.timeout_s,
            "supports_streaming": self.supports_streaming,
            "supports_cancellation": self.supports_cancellation,
            "durable": self.durable,
        }

    def validate_args(self, args: dict) -> tuple[bool, Optional[str]]:
        """Minimal JSON-schema-ish validation against input_schema.properties."""
        if not isinstance(args, dict):
            return False, "arguments must be an object"
        props = (self.input_schema or {}).get("properties") or {}
        required = (self.input_schema or {}).get("required") or []
        for key in required:
            if key not in args:
                return False, f"missing required argument: {key}"
        for key, val in args.items():
            if key not in props:
                # allow extra keys but warn via soft reject of unknown required shapes
                continue
            expected = props[key].get("type")
            if expected == "string" and not isinstance(val, str):
                return False, f"{key} must be a string"
            if expected == "integer" and not isinstance(val, int):
                return False, f"{key} must be an integer"
            if expected == "boolean" and not isinstance(val, bool):
                return False, f"{key} must be a boolean"
            if expected == "array" and not isinstance(val, list):
                return False, f"{key} must be an array"
            if expected == "object" and not isinstance(val, dict):
                return False, f"{key} must be an object"
            max_len = props[key].get("maxLength")
            if max_len is not None and isinstance(val, str) and len(val) > max_len:
                return False, f"{key} exceeds maxLength {max_len}"
        return True, None


# ── Registry ──────────────────────────────────────────────────────────────────

AGENT_TOOL_REGISTRY: dict[str, AgentTool] = {}


def register_agent_tool(tool: AgentTool) -> AgentTool:
    AGENT_TOOL_REGISTRY[tool.name] = tool
    return tool


def get_agent_tool(name: str) -> Optional[AgentTool]:
    return AGENT_TOOL_REGISTRY.get(name)


def list_agent_tools(mode: Optional[AgentMode] = None) -> list[dict]:
    if mode is None:
        return [t.to_dict() for t in AGENT_TOOL_REGISTRY.values()]
    allowed = MODE_TOOLS.get(mode, set())
    return [t.to_dict() for name, t in AGENT_TOOL_REGISTRY.items() if name in allowed]


def tools_for_prompt(mode: AgentMode) -> str:
    """Compact tool list for LLM system prompt."""
    lines = []
    for t in list_agent_tools(mode):
        schema = json.dumps(t["input_schema"], separators=(",", ":"))
        lines.append(
            f"- {t['name']}: {t['description']} "
            f"[side_effect={t['side_effect']}, risk={t['risk']}] schema={schema}"
        )
    return "\n".join(lines)


# ── Tool definitions ──────────────────────────────────────────────────────────

def _s(props: dict, required: list | None = None, **extra) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    schema.update(extra)
    return schema


register_agent_tool(AgentTool(
    name="list_files",
    description="List files and directories in the project workspace (optional path prefix).",
    input_schema=_s({
        "path": {"type": "string", "description": "Relative directory path (default: root)", "maxLength": 512},
        "max_entries": {"type": "integer", "description": "Max entries to return (default 200)"},
    }),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))

register_agent_tool(AgentTool(
    name="read_file",
    description="Read a text file from the project workspace.",
    input_schema=_s({
        "path": {"type": "string", "description": "Relative file path", "maxLength": 512},
        "start_line": {"type": "integer", "description": "Optional 1-based start line"},
        "end_line": {"type": "integer", "description": "Optional 1-based end line"},
    }, required=["path"]),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))

register_agent_tool(AgentTool(
    name="search_files",
    description="Search project filenames and file contents for a query string.",
    input_schema=_s({
        "query": {"type": "string", "description": "Search query", "maxLength": 256},
        "max_results": {"type": "integer", "description": "Max results (default 20)"},
        "glob": {"type": "string", "description": "Optional filename glob filter", "maxLength": 128},
    }, required=["query"]),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=30,
))

register_agent_tool(AgentTool(
    name="get_file_metadata",
    description="Get metadata (size, type, modified) for a path without reading content.",
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
    }, required=["path"]),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=5,
))

register_agent_tool(AgentTool(
    name="create_file",
    description="Create a new file with optional initial content. Fails if file already exists.",
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
        "content": {"type": "string", "description": "Initial content", "maxLength": 2_000_000},
    }, required=["path"]),
    capability="ucip:filesystem.write",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=10,
    durable=True,
))

register_agent_tool(AgentTool(
    name="apply_patch",
    description=(
        "Apply a structured patch to an existing file. Provide either unified_diff "
        "or old_text/new_text for a single contiguous replacement. Fails with "
        "patch_conflict if the file content no longer matches expected context."
    ),
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
        "unified_diff": {"type": "string", "description": "Unified diff text", "maxLength": 2_000_000},
        "old_text": {"type": "string", "description": "Exact context expected in file", "maxLength": 2_000_000},
        "new_text": {"type": "string", "description": "Replacement for old_text", "maxLength": 2_000_000},
        "expected_hash": {"type": "string", "description": "Optional sha256 of current content", "maxLength": 64},
    }, required=["path"]),
    capability="ucip:filesystem.write",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=15,
    durable=True,
))

register_agent_tool(AgentTool(
    name="replace_text",
    description="Replace all or first occurrence of a string in a file.",
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
        "old_text": {"type": "string", "maxLength": 500_000},
        "new_text": {"type": "string", "maxLength": 500_000},
        "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"},
    }, required=["path", "old_text", "new_text"]),
    capability="ucip:filesystem.write",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=10,
    durable=True,
))

register_agent_tool(AgentTool(
    name="rename_file",
    description="Rename or move a file within the project workspace.",
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
        "new_path": {"type": "string", "maxLength": 512},
    }, required=["path", "new_path"]),
    capability="ucip:filesystem.write",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=10,
    durable=True,
))

register_agent_tool(AgentTool(
    name="delete_file",
    description="Delete a file from the project workspace. Irreversible without VCS history.",
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
    }, required=["path"]),
    capability="ucip:filesystem.delete",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.HIGH,
    timeout_s=10,
    durable=True,
))

register_agent_tool(AgentTool(
    name="run_command",
    description="Run a shell command in the project directory under existing sandbox policy.",
    input_schema=_s({
        "command": {"type": "string", "maxLength": 4000},
        "timeout_s": {"type": "integer", "description": "Override timeout (max 120)"},
    }, required=["command"]),
    capability="ucip:execution.shell",
    side_effect=SideEffect.UNKNOWN,
    risk=ToolRisk.HIGH,
    timeout_s=60,
    supports_streaming=True,
    supports_cancellation=True,
    durable=True,
))

register_agent_tool(AgentTool(
    name="run_tests",
    description="Run the project's test suite (pytest/npm test/etc. heuristic or explicit command).",
    input_schema=_s({
        "command": {"type": "string", "description": "Optional explicit test command", "maxLength": 2000},
        "path": {"type": "string", "description": "Optional test path/filter", "maxLength": 512},
    }),
    capability="ucip:execution.shell",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=120,
    supports_streaming=True,
    supports_cancellation=True,
    durable=True,
))

register_agent_tool(AgentTool(
    name="run_build",
    description="Run the project build command.",
    input_schema=_s({
        "command": {"type": "string", "description": "Optional explicit build command", "maxLength": 2000},
    }),
    capability="ucip:execution.shell",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=180,
    supports_streaming=True,
    supports_cancellation=True,
    durable=True,
))

register_agent_tool(AgentTool(
    name="run_linter",
    description="Run linter/typecheck for the project or a path.",
    input_schema=_s({
        "command": {"type": "string", "maxLength": 2000},
        "path": {"type": "string", "maxLength": 512},
    }),
    capability="ucip:execution.shell",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.LOW,
    timeout_s=60,
    supports_streaming=True,
    supports_cancellation=True,
    durable=True,
))

register_agent_tool(AgentTool(
    name="git_status",
    description="Show git status for the project repository.",
    input_schema=_s({}),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=15,
))

register_agent_tool(AgentTool(
    name="git_diff",
    description="Show git diff (optional path).",
    input_schema=_s({
        "path": {"type": "string", "maxLength": 512},
        "staged": {"type": "boolean", "description": "Show staged diff only"},
    }),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=30,
))

register_agent_tool(AgentTool(
    name="git_log",
    description="Show recent commit history.",
    input_schema=_s({
        "limit": {"type": "integer", "description": "Number of commits (default 20)"},
    }),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=15,
))

register_agent_tool(AgentTool(
    name="git_show",
    description="Show a specific commit.",
    input_schema=_s({
        "ref": {"type": "string", "maxLength": 128},
    }, required=["ref"]),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=15,
))

register_agent_tool(AgentTool(
    name="git_branch",
    description="List branches or show current branch.",
    input_schema=_s({}),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))

register_agent_tool(AgentTool(
    name="git_add",
    description="Stage files for commit.",
    input_schema=_s({
        "paths": {"type": "array", "description": "Paths to stage; empty = stage all"},
    }),
    capability="ucip:vcs.write",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=15,
    durable=True,
))

register_agent_tool(AgentTool(
    name="git_commit",
    description="Create a local git commit with the given message. Does not push.",
    input_schema=_s({
        "message": {"type": "string", "maxLength": 2000},
    }, required=["message"]),
    capability="ucip:vcs.write",
    side_effect=SideEffect.LOCAL,
    risk=ToolRisk.MEDIUM,
    timeout_s=30,
    durable=True,
))

register_agent_tool(AgentTool(
    name="get_job",
    description="Get status of an ExecutionJob by id.",
    input_schema=_s({
        "job_id": {"type": "string", "maxLength": 64},
    }, required=["job_id"]),
    capability=None,
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=5,
))

register_agent_tool(AgentTool(
    name="get_job_logs",
    description="Get logs/output for an ExecutionJob.",
    input_schema=_s({
        "job_id": {"type": "string", "maxLength": 64},
    }, required=["job_id"]),
    capability=None,
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))

register_agent_tool(AgentTool(
    name="get_evidence",
    description="Fetch Evidence records related to a correlation or job id.",
    input_schema=_s({
        "correlation_id": {"type": "string", "maxLength": 64},
        "job_id": {"type": "string", "maxLength": 64},
        "limit": {"type": "integer"},
    }),
    capability=None,
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))

register_agent_tool(AgentTool(
    name="list_workflows",
    description="List available workflows for the current user/tenant.",
    input_schema=_s({
        "limit": {"type": "integer"},
    }),
    capability=None,
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))

register_agent_tool(AgentTool(
    name="inspect_workflow",
    description="Inspect a workflow definition by id.",
    input_schema=_s({
        "workflow_id": {"type": "string", "maxLength": 64},
    }, required=["workflow_id"]),
    capability=None,
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=10,
))


register_agent_tool(AgentTool(
    name="get_project_metadata",
    description="Summarize project root: detected languages, config files, approximate size.",
    input_schema=_s({}),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=15,
))

register_agent_tool(AgentTool(
    name="get_test_files",
    description="List likely test files in the project (heuristic by path/name).",
    input_schema=_s({
        "max_results": {"type": "integer"},
    }),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=20,
))

register_agent_tool(AgentTool(
    name="get_build_system",
    description="Detect build/test commands from package.json, pyproject, Makefile, etc.",
    input_schema=_s({}),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=15,
))

register_agent_tool(AgentTool(
    name="get_package_dependencies",
    description="Read dependency declarations from package.json / requirements / pyproject.",
    input_schema=_s({
        "max_entries": {"type": "integer"},
    }),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=15,
))

register_agent_tool(AgentTool(
    name="find_symbol",
    description="Heuristic search for a symbol name (function/class/def) across the project.",
    input_schema=_s({
        "symbol": {"type": "string", "maxLength": 256},
        "max_results": {"type": "integer"},
    }, required=["symbol"]),
    capability="ucip:filesystem.read",
    side_effect=SideEffect.NONE,
    risk=ToolRisk.LOW,
    timeout_s=30,
))

register_agent_tool(AgentTool(
    name="execute_workflow",
    description="Enqueue a workflow execution under existing governance.",
    input_schema=_s({
        "workflow_id": {"type": "string", "maxLength": 64},
        "input": {"type": "object", "description": "Workflow input payload"},
    }, required=["workflow_id"]),
    capability=None,  # workflow path has its own UCIP/HITL gates
    side_effect=SideEffect.UNKNOWN,
    risk=ToolRisk.HIGH,
    timeout_s=30,
    durable=True,
))


# Map agent tool names → UCIP ACTION_TO_CAP keys where needed
AGENT_ACTION_TO_CAP: dict[str, Optional[str]] = {
    t.name: t.capability for t in AGENT_TOOL_REGISTRY.values()
}


# ── Patch helpers ─────────────────────────────────────────────────────────────

def apply_unified_diff(content: str, unified_diff: str) -> tuple[bool, str, Optional[str]]:
    """
    Apply a simple unified diff to content.
    Returns (ok, new_content_or_old, error).
    Supports single-file hunks only; does not implement full patch(1).
    """
    if not unified_diff or not unified_diff.strip():
        return False, content, "empty diff"

    lines = content.splitlines(keepends=True)
    # Normalize to list without forcing keepends complexity
    src = content.splitlines(keepends=True)
    if content and not content.endswith("\n"):
        # difflib-style handling
        pass

    hunks = []
    cur = None
    for line in unified_diff.splitlines():
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                return False, content, f"invalid hunk header: {line}"
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            cur = {"old_start": old_start, "old_count": old_count,
                   "new_start": new_start, "new_count": new_count,
                   "lines": []}
            hunks.append(cur)
        elif cur is not None:
            if line.startswith("\\"):
                continue
            if line[:1] in (" ", "+", "-"):
                cur["lines"].append(line)
            elif line.startswith("---") or line.startswith("+++"):
                continue
            else:
                # tolerate missing prefix as context
                cur["lines"].append(" " + line)

    if not hunks:
        return False, content, "no hunks found in diff"

    # Apply from bottom to top so offsets stay valid
    result = content.splitlines(keepends=True)
    # Ensure we work with lines without forcing trailing newline semantics inconsistently
    plain = content.splitlines()
    has_trailing_nl = content.endswith("\n")

    for hunk in reversed(hunks):
        old_start = hunk["old_start"] - 1  # 0-based
        old_lines = []
        new_lines = []
        for hl in hunk["lines"]:
            tag, body = hl[0], hl[1:]
            if tag == " ":
                old_lines.append(body)
                new_lines.append(body)
            elif tag == "-":
                old_lines.append(body)
            elif tag == "+":
                new_lines.append(body)

        end = old_start + len(old_lines)
        if end > len(plain):
            return False, content, "patch_conflict: hunk extends past end of file"
        actual = plain[old_start:end]
        if actual != old_lines:
            return False, content, (
                "patch_conflict: context does not match current file content "
                f"at line {old_start + 1}"
            )
        plain = plain[:old_start] + new_lines + plain[end:]

    new_content = "\n".join(plain)
    if has_trailing_nl or content.endswith("\n"):
        if not new_content.endswith("\n") and (has_trailing_nl or plain):
            new_content += "\n"
    return True, new_content, None


def apply_context_replace(content: str, old_text: str, new_text: str) -> tuple[bool, str, Optional[str]]:
    if old_text not in content:
        return False, content, "patch_conflict: old_text not found in file"
    # Single replacement of first occurrence for safety when used as patch
    new_content = content.replace(old_text, new_text, 1)
    return True, new_content, None


def make_line_diff(old: str, new: str) -> list[dict]:
    """Produce simple line diff records for UI review."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    out = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            out.append({"type": "add", "text": line[1:]})
        elif line.startswith("-"):
            out.append({"type": "del", "text": line[1:]})
        else:
            out.append({"type": "ctx", "text": line[1:] if line.startswith(" ") else line})
    return out
