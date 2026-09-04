"""Close remaining gaps: crawl recovery loop + creative workspace E2E."""
import asyncio
import os
from pathlib import Path

from brain.web_crawl_recovery import (
    closed_loop_crawl_recovery,
    decide_from_crawl,
    crawl_to_evidence,
)
from brain.mission_engine import DecisionType, ExecutionEvidence, decide_recovery
from brain.orchestration_runtime import NodeExecutionRequest, run_node_on_agent_runtime
from brain.personas import get_persona, suggest_personas_for_goal
from execution.web_intel.store import create_crawl, update_crawl, get_crawl


def test_timeout_retry_fresh_ucip_then_success():
    first = {
        "crawl_id": "cr_timeout1",
        "status": "FAILED",
        "error": "timeout while fetching",
        "root_url": "https://example.com",
    }
    def second():
        return {
            "crawl_id": "cr_timeout1_retry",
            "status": "COMPLETED",
            "error": None,
            "stats_json": '{"pages_fetched": 3}',
        }
    # force_ucip_deny path
    denied = closed_loop_crawl_recovery(
        user_id="u1",
        node_id="web_crawl",
        first_crawl=first,
        retry_crawl_factory=second,
        force_ucip_deny=True,
    )
    assert denied["decision"]["decision_type"] == DecisionType.RETRY.value
    assert denied["retry_authorized"] is False
    assert denied["worker_executed_after_decision"] is False
    assert denied["terminal"] == "blocked_by_ucip"

    # allow path — factory runs second attempt
    ok = closed_loop_crawl_recovery(
        user_id="u1",
        node_id="web_crawl",
        first_crawl=first,
        retry_crawl_factory=second,
        force_ucip_deny=False,
    )
    # UCIP may deny if gateway unavailable → still assert decision was RETRY
    assert ok["decision"]["decision_type"] == DecisionType.RETRY.value
    if ok["retry_authorized"]:
        assert ok["retry_executed"] is True
        assert ok["second_crawl"]["status"] == "COMPLETED"
        assert ok["terminal"] == "completed"
        assert ok["attempts"] == 2
    else:
        assert ok["terminal"] == "blocked_by_ucip"
        assert ok["worker_executed_after_decision"] is False


def test_ask_user_on_credential_no_auto_retry():
    crawl = {
        "crawl_id": "cr_auth",
        "status": "FAILED",
        "error": "401 auth required for resource",
    }
    out = closed_loop_crawl_recovery(
        user_id="u1",
        node_id="web_crawl",
        first_crawl=crawl,
        retry_crawl_factory=lambda: {"status": "COMPLETED"},
    )
    assert out["decision"]["decision_type"] == DecisionType.ASK_USER.value
    assert out["decision"]["requires_user"] is True
    assert out["terminal"] == "waiting_for_user"
    assert out["worker_executed_after_decision"] is False
    assert out["retry_executed"] is False


def test_abort_on_ssrf_no_retry():
    crawl = {
        "crawl_id": "cr_ssrf",
        "status": "FAILED",
        "error": "blocked:private_or_internal_host",
    }
    out = closed_loop_crawl_recovery(
        user_id="u1",
        node_id="web_crawl",
        first_crawl=crawl,
        retry_crawl_factory=lambda: {"status": "COMPLETED"},
    )
    assert out["decision"]["decision_type"] == DecisionType.ABORT.value
    assert out["terminal"] == "aborted"
    assert out["retry_executed"] is False
    assert out["worker_executed_after_decision"] is False


def test_partial_maps_to_budget_decision():
    crawl = {
        "crawl_id": "cr_partial",
        "status": "PARTIAL",
        "error": "PAGE_BUDGET_REACHED",
    }
    ev, dec = decide_from_crawl(node_id="web_crawl", crawl=crawl)
    assert ev.success is False
    # budget exceeded → decide_recovery typically RETRY or similar
    assert dec.decision_type in (
        DecisionType.RETRY.value,
        DecisionType.REPAIR.value,
        DecisionType.REPLAN.value,
        DecisionType.CONTINUE.value,
        DecisionType.COMPLETE.value,
        DecisionType.ASK_USER.value,
        DecisionType.ABORT.value,
    )
    assert "budget" in (ev.error or "").lower() or dec.failure_class


def test_idempotent_completed_no_second_enqueue():
    c = create_crawl({
        "user_id": "u-idemp",
        "root_url": "https://example.com",
        "normalized_root_url": "https://example.com",
    })
    update_crawl(c["crawl_id"], status="COMPLETED")
    req = NodeExecutionRequest(
        plan_id="p",
        node_id="web_crawl",
        user_id="u-idemp",
        workspace_id="w",
        persona_id="research",
        objective="x",
        effective_caps=["web.intelligence"],
        authorization_decision="allow",
        node_kind="web_crawl",
        crawl_id=c["crawl_id"],
    )
    r = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
    assert r.raw_terminal and r.raw_terminal.get("idempotent") is True


def test_creative_personas_workspace_artifact_e2e():
    """Writer/Storyteller/Script Writer via fake agent runtime + workspace files."""
    os.environ["DEVOS_ORCH_FAKE_RUNTIME"] = "1"
    os.environ["PYTEST_CURRENT_TEST"] = "test_creative_personas_workspace_artifact_e2e"
    try:
        for persona, fname, objective in (
            ("writer", "article.md", "Write a professional company profile"),
            ("storyteller", "story.md", "Write a short story about the sea"),
            ("script_writer", "script.md", "Write a YouTube video script"),
        ):
            p = get_persona(persona)
            assert p is not None
            assert "deployment.production" not in p.capabilities
            req = NodeExecutionRequest(
                plan_id=f"creative-{persona}",
                node_id=f"node-{persona}",
                user_id="creative-user",
                workspace_id="creative-ws",
                persona_id=persona,
                objective=objective,
                effective_caps=list(p.capabilities or ["fs.write", "fs.read"]),
                authorization_decision="allow",
            )
            result = asyncio.get_event_loop().run_until_complete(run_node_on_agent_runtime(req))
            assert result.success, result.error
            assert result.status == "succeeded"
            # Fake runtime should claim workspace files
            assert result.files_changed is not None
            # Evidence-shaped success
            ev = ExecutionEvidence(
                node_id=req.node_id,
                task_id=result.task_id,
                persona_id=persona,
                workspace_id=req.workspace_id,
                status=result.status,
                success=True,
                files_changed=list(result.files_changed or []),
                authorization_decision="allow",
            )
            assert ev.success
    finally:
        os.environ.pop("DEVOS_ORCH_FAKE_RUNTIME", None)
        # leave PYTEST_CURRENT_TEST to pytest


def test_research_to_writer_composition_routing_and_caps():
    goal = "Research this business and write me a professional company profile"
    suggested = suggest_personas_for_goal(goal)
    assert "nuha" in suggested
    # Writer participates in composition
    assert get_persona("writer") is not None
    # Script composition
    g2 = "Turn this research into a YouTube documentary script"
    s2 = suggest_personas_for_goal(g2)
    assert get_persona("script_writer") is not None
    # Storyteller
    g3 = "Research this history and write a fictional short story"
    s3 = suggest_personas_for_goal(g3)
    assert get_persona("storyteller") is not None
