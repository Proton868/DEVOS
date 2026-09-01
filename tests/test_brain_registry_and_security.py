import pytest

from brain.agents import describe_worker_selection, get_best_agent_for_goal
from governance.tool_contracts import (
    FailureMode,
    ToolContract,
    ToolValidator,
    get_tool_contract,
    register_tool_contract,
)
from governance.ucip import AgentIdentity, ExecutionPermissionGuard, TrustLevel


def test_worker_selection_explains_the_routing_decision():
    explanation = describe_worker_selection("build a React dashboard with Next.js and Tailwind")
    assert "frontend-developer" in explanation
    assert "routing" in explanation.lower()


def test_tool_registry_allows_dynamic_registration_for_new_capabilities():
    contract = ToolContract(
        name="custom_tool",
        description="A sample plugin tool",
        required_cap="ucip:execution.python",
        input_fields=["value"],
        output_fields=["value"],
        timeout_s=5,
        failure_mode=FailureMode.SKIP,
        max_input_len=200,
        allow_network=False,
        is_reversible=True,
    )

    register_tool_contract(contract)

    assert get_tool_contract("custom_tool") is contract
    valid, error = ToolValidator.validate("custom_tool", '{"value": 1}')
    assert valid, error


def test_execution_permission_guard_blocks_untrusted_execution():
    identity = AgentIdentity.create("user", "session", TrustLevel.READ_ONLY)
    guard = ExecutionPermissionGuard()

    allowed, reason = guard.can_execute(identity, "write_python", "print('hi')")
    assert not allowed
    assert "permission" in reason.lower()
