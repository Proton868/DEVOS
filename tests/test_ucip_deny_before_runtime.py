"""UCIP / specialty deny must not invoke Agent Runtime."""
import asyncio
from brain.specialty_policy import evaluate_node_request
from brain.capability_canon import canonicalize, aliases_are_same_authority
from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
from governance.ucip import ALWAYS_BLOCKED_CAPS


def test_research_shell_denied_by_specialty():
    d = evaluate_node_request(persona_id="research", requested_caps={"shell.exec"})
    assert d.allow is False or "shell.exec" in d.denied_caps


def test_alias_same_as_ucip_form():
    assert aliases_are_same_authority("fs.read", "ucip:filesystem.read")
    assert canonicalize("fs.write") == "filesystem.write"


def test_always_blocked_present():
    assert len(ALWAYS_BLOCKED_CAPS) > 0


def test_deny_authorization_skips_runtime():
    """Deny returns before AgentRuntime import/execution."""
    req = NodeExecutionRequest(
        plan_id="p", node_id="n", user_id="u", workspace_id="w",
        persona_id="web", objective="x", authorization_decision="deny",
    )
    r = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert r.success is False
    assert r.status == "blocked"
    assert "not authorized" in (r.error or "").lower() or r.status == "blocked"
    # No task id means runtime loop was not entered
    assert r.task_id is None


def test_unknown_capability_not_expanded():
    d = evaluate_node_request(persona_id="research", requested_caps={"totally.unknown.cap"})
    assert d.allow is False or "totally.unknown.cap" in d.denied_caps
