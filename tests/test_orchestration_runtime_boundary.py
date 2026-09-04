"""Integration boundary tests — deterministic FAKE runtime (not live LLM)."""
import os
import asyncio

os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"

from brain.orchestration_runtime import (
    NodeExecutionRequest,
    run_node_on_agent_runtime,
)
from brain.capability_canon import canonicalize, aliases_are_same_authority
from brain.specialty_policy import evaluate_node_request
from brain.orchestration_dag import (
    OrchestrationNode,
    OrchestrationEdge,
    compute_readiness,
    NodeStatus,
    DepCondition,
)


def test_fake_runtime_returns_task_and_files():
    req = NodeExecutionRequest(
        plan_id="p1",
        node_id="s1",
        user_id="user-test",
        workspace_id="ws-test",
        persona_id="web",
        objective="Build a one-page shoe website",
        effective_caps=["filesystem.write"],
        authorization_decision="allow",
    )
    result = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert result.success is True
    assert result.task_id
    assert result.status == "succeeded"
    assert any(
        (f.get("path") if isinstance(f, dict) else f) == "index.html"
        for f in (result.files_changed or [])
    )


def test_unauthorized_never_runs():
    req = NodeExecutionRequest(
        plan_id="p1",
        node_id="s1",
        user_id="user-test",
        workspace_id="ws-test",
        persona_id="web",
        objective="delete production",
        authorization_decision="deny",
    )
    result = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert result.success is False
    assert result.status == "blocked"


def test_missing_workspace_rejected():
    req = NodeExecutionRequest(
        plan_id="p1",
        node_id="s1",
        user_id="user-test",
        workspace_id="",
        persona_id="web",
        objective="build site",
        authorization_decision="allow",
    )
    result = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert result.success is False
    assert "workspace" in (result.error or "")


def test_research_denied_shell_before_runtime():
    d = evaluate_node_request(persona_id="research", requested_caps={"shell.exec"})
    assert d.allow is False or "shell.exec" in d.denied_caps


def test_alias_authority_equivalence():
    assert aliases_are_same_authority("fs.read", "ucip:filesystem.read")
    assert canonicalize("fs.write") == "filesystem.write"


def test_dag_independent_ready_after_parent():
    a = OrchestrationNode(id="a", description="a", persona_id="web", capabilities=["fs.read"])
    b = OrchestrationNode(id="b", description="b", persona_id="web", dependencies=["a"], capabilities=["fs.write"])
    c = OrchestrationNode(id="c", description="c", persona_id="code", dependencies=["a"], capabilities=["fs.read"])
    edges = [
        OrchestrationEdge("a", "b", DepCondition.VERIFIED.value),
        OrchestrationEdge("a", "c", DepCondition.VERIFIED.value),
    ]
    a.status = NodeStatus.VERIFIED.value
    a.verification_evidence = {"ok": True}
    ready = compute_readiness([a, b, c], edges)
    assert "b" in ready and "c" in ready
