"""
DevOS Tool Contracts — Gap #6: Structured Tool Execution Protocol.
═══════════════════════════════════════════════════════════════════════════════
Every tool the Brain can call has a formal contract defining:
  - input_schema (validated before execution)
  - output_schema (validated after execution)
  - timeout
  - retry_policy
  - failure_mode (abort | escalate | skip)
  - required_cap (UCIP capability token)

LLM output NEVER directly executes. It passes through:
  1. Schema validation
  2. UCIP policy gate
  3. Injection scanner
  4. Only then reaches ExecutionLayer

This is the "deterministic wrapper" required by the audit.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("devos.tools")


class FailureMode(str, Enum):
    ABORT     = "abort"      # Stop the loop
    ESCALATE  = "escalate"   # Ask user
    SKIP      = "skip"       # Continue without result
    RETRY     = "retry"      # Retry up to policy max


@dataclass
class RetryPolicy:
    max_attempts:    int   = 3
    backoff_s:       float = 1.0
    backoff_mult:    float = 2.0
    max_backoff_s:   float = 30.0


@dataclass
class ToolContract:
    name:           str
    description:    str
    required_cap:   Optional[str]        # UCIP capability token
    input_fields:   list[str]            # Required input keys
    output_fields:  list[str]            # Expected output keys
    timeout_s:      int   = 30
    retry_policy:   RetryPolicy = field(default_factory=RetryPolicy)
    failure_mode:   FailureMode = FailureMode.RETRY
    max_input_len:  int   = 8000
    allow_network:  bool  = False
    is_reversible:  bool  = True         # False = always needs HITL confirm

    def validate_input(self, action_input: str) -> tuple[bool, Optional[str]]:
        """Validate the action_input string before execution."""
        if not action_input or not action_input.strip():
            return False, "action_input is empty"
        if len(action_input) > self.max_input_len:
            return False, f"input too long: {len(action_input)} > {self.max_input_len}"
        return True, None

    def validate_output(self, output: dict) -> tuple[bool, list[str]]:
        """Validate execution output has expected fields."""
        missing = [f for f in self.output_fields if f not in output]
        return len(missing) == 0, missing

    def to_dict(self) -> dict:
        return {
            "name":          self.name,
            "description":   self.description,
            "required_cap":  self.required_cap,
            "input_fields":  self.input_fields,
            "output_fields": self.output_fields,
            "timeout_s":     self.timeout_s,
            "failure_mode":  self.failure_mode,
            "is_reversible": self.is_reversible,
            "allow_network": self.allow_network,
        }


# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, ToolContract] = {

    "write_python": ToolContract(
        name="write_python",
        description="Execute Python code in a sandboxed environment",
        required_cap="ucip:execution.python",
        input_fields=["code"],
        output_fields=["status", "stdout", "exit_code", "duration_ms"],
        timeout_s=60,
        retry_policy=RetryPolicy(max_attempts=3, backoff_s=1.0),
        failure_mode=FailureMode.RETRY,
        max_input_len=16_000,
        allow_network=False,
        is_reversible=True,
    ),

    "write_bash": ToolContract(
        name="write_bash",
        description="Execute Bash script in a sandboxed environment",
        required_cap="ucip:execution.bash",
        input_fields=["code"],
        output_fields=["status", "stdout", "exit_code", "duration_ms"],
        timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=2.0),
        failure_mode=FailureMode.RETRY,
        max_input_len=8_000,
        allow_network=False,
        is_reversible=True,
    ),

    "write_node": ToolContract(
        name="write_node",
        description="Execute Node.js code in a sandboxed environment",
        required_cap="ucip:execution.node",
        input_fields=["code"],
        output_fields=["status", "stdout", "exit_code", "duration_ms"],
        timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=1.0),
        failure_mode=FailureMode.RETRY,
        max_input_len=8_000,
        allow_network=False,
        is_reversible=True,
    ),

    "search_web": ToolContract(
        name="search_web",
        description="Search the web via Tavily or SearXNG",
        required_cap="ucip:search.web",
        input_fields=["query"],
        output_fields=["results"],
        timeout_s=20,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=1.0),
        failure_mode=FailureMode.SKIP,
        max_input_len=500,
        allow_network=True,
        is_reversible=True,
    ),

    "recall_memory": ToolContract(
        name="recall_memory",
        description="Recall relevant memories from persistent store",
        required_cap="ucip:memory.read",
        input_fields=["query"],
        output_fields=["memories"],
        timeout_s=10,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=0.5),
        failure_mode=FailureMode.SKIP,
        max_input_len=500,
        allow_network=False,
        is_reversible=True,
    ),

    "delete_file": ToolContract(
        name="delete_file",
        description="Delete a file from the filesystem",
        required_cap="ucip:filesystem.delete",
        input_fields=["path"],
        output_fields=["deleted", "path"],
        timeout_s=5,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ESCALATE,
        max_input_len=256,
        allow_network=False,
        is_reversible=False,    # Irreversible → HITL gate
    ),

    "write_file": ToolContract(
        name="write_file",
        description="Write/overwrite a file inside a project directory",
        required_cap="ucip:filesystem.write",
        input_fields=["path", "content"],
        output_fields=["path", "size", "written_at"],
        timeout_s=10,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=0.5),
        failure_mode=FailureMode.RETRY,
        max_input_len=2_000_000,
        allow_network=False,
        is_reversible=True,     # overwrite is recoverable via git/version history, unlike delete
    ),

    "read_file": ToolContract(
        name="read_file",
        description="Read a file inside a project directory",
        required_cap="ucip:filesystem.read",
        input_fields=["path"],
        output_fields=["path", "content", "size"],
        timeout_s=5,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=0.5),
        failure_mode=FailureMode.SKIP,
        max_input_len=256,
        allow_network=False,
        is_reversible=True,
    ),

    "git_commit": ToolContract(
        name="git_commit",
        description="Stage and commit changes in a project's local git repo",
        required_cap="ucip:vcs.write",
        input_fields=["message"],
        output_fields=["success", "stdout", "exit_code"],
        timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ABORT,
        max_input_len=2000,
        allow_network=False,
        is_reversible=True,     # local commits are reversible (reset/revert)
    ),

    "git_push": ToolContract(
        name="git_push",
        description="Push committed changes to a remote repository",
        required_cap="ucip:vcs.push",
        input_fields=["remote", "branch"],
        output_fields=["success", "stdout", "exit_code"],
        timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ESCALATE,
        max_input_len=256,
        allow_network=True,
        is_reversible=False,    # publishes outside the sandbox → HITL gate for autonomous use
    ),

    "git_discard": ToolContract(
        name="git_discard",
        description="Discard uncommitted working-tree changes to a file, reverting to HEAD",
        required_cap="ucip:vcs.write",
        input_fields=["path"],
        output_fields=["success", "stdout", "exit_code"],
        timeout_s=10,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ESCALATE,
        max_input_len=256,
        allow_network=False,
        is_reversible=False,    # uncommitted work is gone for good — HITL gate
    ),

    "run_terminal": ToolContract(
        name="run_terminal",
        description="Run a shell command inside a project directory (direct human IDE use; "
                     "autonomous agent shell execution continues to use write_bash instead)",
        required_cap="ucip:execution.shell",
        input_fields=["command"],
        output_fields=["status", "stdout", "stderr", "exit_code"],
        timeout_s=60,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ABORT,
        max_input_len=4000,
        allow_network=True,
        is_reversible=True,
    ),

    "spawn_agent": ToolContract(
        name="spawn_agent",
        description="Delegate a sub-task to another Worker persona (Worker-to-Worker "
                     "delegation, see workers/runtime.py and cognitive/coordinator.py). "
                     "Always HITL-gated (ucip:agent.spawn is in HITL_REQUIRED_CAPS) and "
                     "additionally depth-limited in core/loop.py to prevent runaway "
                     "recursive spawning even after human approval.",
        required_cap="ucip:agent.spawn",
        input_fields=["worker", "goal"],
        output_fields=["output", "success"],
        timeout_s=180,   # a sub-worker runs its own full loop, needs real headroom
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ESCALATE,
        max_input_len=2000,
        allow_network=True,
        is_reversible=False,
    ),

    "graph_remember": ToolContract(
        name="graph_remember",
        description="Record a fact as an entity relationship in the knowledge graph "
                     "(Memory's Semantic division, see memory/graph.py). E.g. "
                     "'Alice works_on Agency OS' — entities are created by name if they "
                     "don't already exist. Reuses ucip:memory.write rather than a separate "
                     "capability, since the graph IS Memory's semantic division, not a "
                     "distinct capability domain.",
        required_cap="ucip:memory.write",
        input_fields=["from_type", "from_name", "to_type", "to_name", "relation_type"],
        output_fields=["from_entity_id", "to_entity_id", "relationship_id"],
        timeout_s=10,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=0.5),
        failure_mode=FailureMode.SKIP,
        max_input_len=1000,
        allow_network=False,
        is_reversible=True,   # can be corrected with a later fact; not destructive like delete_file
    ),

    "graph_query": ToolContract(
        name="graph_query",
        description="Look up an entity by name and traverse its recorded relationships "
                     "in the knowledge graph. Distinguishes 'not in the graph at all' from "
                     "'in the graph but nothing matches' (see memory/graph.py's query_by_name).",
        required_cap="ucip:memory.read",
        input_fields=["name"],
        output_fields=["found", "entity", "related"],
        timeout_s=10,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=0.5),
        failure_mode=FailureMode.SKIP,
        max_input_len=500,
        allow_network=False,
        is_reversible=True,
    ),

    "call_api": ToolContract(
        name="call_api",
        description="Make an outbound HTTP API call",
        required_cap="ucip:api.call",
        input_fields=["url", "method"],
        output_fields=["status_code", "body"],
        timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=2, backoff_s=2.0),
        failure_mode=FailureMode.RETRY,
        max_input_len=2000,
        allow_network=True,
        is_reversible=True,
    ),

    "mark_complete": ToolContract(
        name="mark_complete",
        description="Declare the goal achieved and return the final answer",
        required_cap=None,
        input_fields=["answer"],
        output_fields=[],
        timeout_s=1,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ABORT,
        max_input_len=10_000,
        is_reversible=True,
    ),

    "ask_user": ToolContract(
        name="ask_user",
        description="Escalate to the user with a question",
        required_cap=None,
        input_fields=["question"],
        output_fields=[],
        timeout_s=1,
        retry_policy=RetryPolicy(max_attempts=1),
        failure_mode=FailureMode.ESCALATE,
        max_input_len=2000,
        is_reversible=True,
    ),
}


def register_tool_contract(contract: ToolContract) -> ToolContract:
    """Register a plugin-defined tool contract without editing the core registry."""
    TOOL_REGISTRY[contract.name] = contract
    return contract


def get_tool_contract(action: str) -> Optional[ToolContract]:
    return TOOL_REGISTRY.get(action)


def list_tool_contracts() -> list[dict]:
    return [contract.to_dict() for contract in TOOL_REGISTRY.values()]


class ToolValidator:
    """
    Validates Brain-generated tool calls before they reach UCIP or Execution.
    This is the "LLM output does NOT directly execute" layer.
    """

    @classmethod
    def validate(cls, action: str, action_input: str) -> tuple[bool, Optional[str]]:
        """
        Full pre-execution validation.
        Returns (valid, error_message).
        """
        # 1. Tool must be registered
        contract = get_tool_contract(action)
        if not contract:
            return False, f"Unknown tool: '{action}'. Valid tools: {list(TOOL_REGISTRY.keys())}"

        # 2. Input validation
        valid, err = contract.validate_input(action_input)
        if not valid:
            return False, f"Tool '{action}' input invalid: {err}"

        # 3. Basic content safety for code execution
        if action in ("write_python", "write_bash", "write_node"):
            safety_ok, safety_err = cls._code_safety_check(action_input, action)
            if not safety_ok:
                return False, safety_err

        return True, None

    @classmethod
    def _code_safety_check(cls, code: str, action: str) -> tuple[bool, Optional[str]]:
        """Pre-execution code safety check (complements sandbox static analysis)."""
        # Check it's actually code (not empty or garbage)
        if len(code.strip()) < 3:
            return False, "Code is too short to be valid"

        # For Python: basic syntax check
        if action == "write_python":
            try:
                import ast
                ast.parse(code)
            except SyntaxError as e:
                return False, f"Python syntax error: {e}"

        return True, None

    @classmethod
    def get_contract(cls, action: str) -> Optional[ToolContract]:
        return get_tool_contract(action)

    @classmethod
    def list_tools(cls) -> list[dict]:
        return list_tool_contracts()
