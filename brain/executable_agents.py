"""
Executable-agent registry for DevOS personas.

Every registered persona is a real agent contract:
  stable ID → role → capabilities → runtime tools → workspace scope
  → AgentRuntime path → evidence → work history → Ponytail when code-bearing.

Does NOT introduce a second runtime. Maps personas onto AgentRuntime tools
and specialty policies. Nuha remains orchestrator (can_delegate=True, no
specialist-only tool loop required for its own role).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Aliases: persona-facing names → AgentRuntime tool names
TOOL_ALIASES: dict[str, str] = {
    "write_file": "create_file",
    "run_terminal": "run_command",
    "run_shell": "run_command",
    "terminal": "run_command",
    "search_web": "list_files",  # no generic web tool in AgentRuntime; research uses read/list
    "spawn_agent": "list_files",  # delegation is via A2A, not a tool
    "graph_remember": "get_evidence",
    "graph_query": "get_evidence",
    "write_python": "create_file",
    "write_bash": "create_file",
    "*": "*",
}


@dataclass(frozen=True)
class ExecutableAgentContract:
    persona_id: str
    agent_id: str  # stable executable id
    role: str  # orchestrator | specialist
    capabilities: tuple[str, ...]
    runtime_tools: tuple[str, ...]  # resolved AgentRuntime tool names
    workspace_scope: tuple[str, ...]
    can_receive_delegation: bool
    can_delegate: bool
    requires_ponytail: bool
    specialty_policy_id: str
    agent_slug: Optional[str] = None
    notes: str = ""

    def to_matrix_row(self) -> dict:
        return {
            "persona_id": self.persona_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "runtime_tools": list(self.runtime_tools),
            "workspace_scope": list(self.workspace_scope),
            "can_receive_delegation": self.can_receive_delegation,
            "can_delegate": self.can_delegate,
            "requires_ponytail": self.requires_ponytail,
            "specialty_policy_id": self.specialty_policy_id,
            "agent_slug": self.agent_slug,
            "notes": self.notes,
        }


def resolve_runtime_tools(allowed_tools: list[str]) -> list[str]:
    """Map advertised tools to AgentRuntime registry names."""
    from brain.agent_tools import AGENT_TOOL_REGISTRY, get_agent_tool

    if not allowed_tools or allowed_tools == ["*"] or "*" in allowed_tools:
        return sorted(AGENT_TOOL_REGISTRY.keys())

    resolved: list[str] = []
    for name in allowed_tools:
        canon = TOOL_ALIASES.get(name, name)
        if canon == "*":
            return sorted(AGENT_TOOL_REGISTRY.keys())
        if get_agent_tool(canon) is not None:
            if canon not in resolved:
                resolved.append(canon)
        elif get_agent_tool(name) is not None:
            if name not in resolved:
                resolved.append(name)
    return resolved


def build_contract_for_persona(persona) -> ExecutableAgentContract:
    from brain.specialty_policy import SPECIALTY_POLICIES

    pid = persona.id
    policy = SPECIALTY_POLICIES.get(pid) or SPECIALTY_POLICIES.get("code")
    scope = tuple(policy.scope_paths) if policy else ("**",)
    policy_id = policy.persona_id if policy else pid

    tools = resolve_runtime_tools(list(persona.allowed_tools or []))
    # Orchestrator: may list all tools for planning visibility but does not
    # execute specialist work via delegation layer.
    is_orch = (persona.role or "") == "orchestrator"
    requires_pt = (not is_orch) and any(
        t in tools
        for t in (
            "create_file",
            "apply_patch",
            "replace_text",
            "delete_file",
            "rename_file",
        )
    )
    # Specialists must receive delegation; orchestrator delegates out
    can_receive = not is_orch
    can_delegate = bool(persona.can_delegate) or is_orch

    caps = tuple(persona.capabilities or [])
    if policy and policy.allow:
        # Intersection: advertised ∩ policy allow (union with policy for enforcement)
        policy_allow = set(policy.allow)
        if caps:
            caps = tuple(sorted(set(caps) | policy_allow))
        else:
            caps = tuple(sorted(policy_allow))

    return ExecutableAgentContract(
        persona_id=pid,
        agent_id=f"agent:{pid}",
        role=persona.role or "specialist",
        capabilities=caps,
        runtime_tools=tuple(tools),
        workspace_scope=scope,
        can_receive_delegation=can_receive,
        can_delegate=can_delegate,
        requires_ponytail=requires_pt,
        specialty_policy_id=policy_id,
        agent_slug=persona.agent_slug,
        notes="executable via AgentRuntime + A2A delegation",
    )


def build_registry() -> dict[str, ExecutableAgentContract]:
    from brain.personas import list_personas

    reg: dict[str, ExecutableAgentContract] = {}
    for p in list_personas():
        reg[p.id] = build_contract_for_persona(p)
    return reg


def capability_matrix() -> list[dict]:
    return [c.to_matrix_row() for c in build_registry().values()]


def verify_contract(contract: ExecutableAgentContract) -> list[str]:
    """Return list of contract violations (empty = OK)."""
    from brain.agent_tools import get_agent_tool
    from brain.specialty_policy import SPECIALTY_POLICIES
    from brain.personas import get_persona

    errors: list[str] = []
    p = get_persona(contract.persona_id)
    if p is None:
        errors.append("persona_missing_from_registry")
        return errors
    if not contract.persona_id or not contract.agent_id:
        errors.append("missing_stable_id")
    if contract.role not in ("orchestrator", "specialist"):
        errors.append(f"invalid_role:{contract.role}")
    if not contract.capabilities:
        errors.append("no_capabilities")
    if contract.role == "specialist" and not contract.runtime_tools:
        errors.append("specialist_has_no_runtime_tools")
    for t in contract.runtime_tools:
        if get_agent_tool(t) is None:
            errors.append(f"tool_not_in_runtime:{t}")
    if contract.specialty_policy_id not in SPECIALTY_POLICIES:
        errors.append(f"missing_specialty_policy:{contract.specialty_policy_id}")
    if contract.role == "specialist" and not contract.can_receive_delegation:
        errors.append("specialist_not_delegatable")
    if contract.role == "orchestrator" and not contract.can_delegate:
        errors.append("orchestrator_cannot_delegate")
    if not contract.workspace_scope:
        errors.append("no_workspace_scope")
    return errors


def verify_all_personas() -> dict[str, list[str]]:
    """persona_id → violations."""
    return {pid: verify_contract(c) for pid, c in build_registry().items()}
