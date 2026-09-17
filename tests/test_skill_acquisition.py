"""Governed dynamic skill/tool acquisition."""
from __future__ import annotations

import pytest

from governance import skill_acquisition as sa
from brain.agent_tools import get_agent_tool, AgentMode, MODE_TOOLS


@pytest.fixture(autouse=True)
def _clean():
    sa.reset_store_for_tests()
    yield
    sa.reset_store_for_tests()


def test_detect_missing_and_present():
    d = sa.detect_missing_capability("definitely_missing_xyz")
    assert d["missing"] is True
    d2 = sa.detect_missing_capability("list_files")
    assert d2["missing"] is False
    assert d2["as_tool"] is True


def test_safe_capability_creation_and_install():
    result = sa.acquire_skill_pipeline(
        name="hash_helper",
        description="Hash text with sha256",
        reason="Need checksum without shell",
        requested_by="coding_agent",
        risk="low",
        side_effect="none",
        handler_kind="hash_text",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )
    assert result["ok"] is True
    prop = result["proposal"]
    assert prop["status"] == "installed"
    assert prop["installed_tool"] == "hash_helper"
    tool = get_agent_tool("hash_helper")
    assert tool is not None
    assert tool.capability == "ucip:skill.hash_helper"
    # execute dynamic handler
    out = sa.run_dynamic_skill_handler("hash_helper", {"text": "abc"})
    assert out and out.get("ok") is True
    assert "sha256" in out
    assert "hash_helper" in MODE_TOOLS[AgentMode.AGENT]


def test_malformed_tool_rejected():
    prop = sa.propose_skill(
        name="BAD NAME",
        description="",
        reason="",
        requested_by="agent",
        handler_kind="not_a_real_handler",
    )
    prop = sa.validate_proposal(prop.proposal_id)
    assert prop.status == sa.ProposalStatus.REJECTED
    assert prop.validation_errors


def test_privileged_requires_approval_not_auto_install():
    result = sa.acquire_skill_pipeline(
        name="net_probe",
        description="Would touch network",
        reason="Need outbound check",
        requested_by="agent",
        risk="high",
        side_effect="network",
        handler_kind="echo",
    )
    assert result["ok"] is False
    assert result["stage"] == "pending_approval"
    prop = result["proposal"]
    assert prop["status"] == "pending_approval"
    assert get_agent_tool("net_probe") is None


def test_privileged_rejected_then_approved_install():
    prop = sa.propose_skill(
        name="sys_echo",
        description="System-adjacent echo",
        reason="ops",
        requested_by="agent",
        risk="high",
        side_effect="system",
        handler_kind="echo",
    )
    prop = sa.validate_proposal(prop.proposal_id)
    assert prop.status == sa.ProposalStatus.VALIDATED
    prop = sa.request_approval(prop.proposal_id)
    assert prop.status == sa.ProposalStatus.PENDING_APPROVAL
    # Without approval, install must not complete
    prop2 = sa.install_skill(prop.proposal_id)
    assert prop2.status == sa.ProposalStatus.PENDING_APPROVAL
    # Approve then install
    sa.resolve_approval(prop.proposal_id, approved=True, resolved_by="operator")
    prop3 = sa.install_skill(prop.proposal_id)
    assert prop3.status == sa.ProposalStatus.INSTALLED
    assert get_agent_tool("sys_echo") is not None


def test_security_rejects_bypass_language():
    prop = sa.propose_skill(
        name="evil_tool",
        description="Try to bypass governance unrestricted",
        reason="hack",
        requested_by="agent",
        risk="low",
        handler_kind="echo",
    )
    prop = sa.validate_proposal(prop.proposal_id)
    assert prop.status == sa.ProposalStatus.REJECTED


def test_denied_approval_blocks_install():
    prop = sa.propose_skill(
        name="need_approval",
        description="High risk skill",
        reason="test",
        requested_by="agent",
        risk="critical",
        side_effect="none",
        handler_kind="echo",
    )
    sa.validate_proposal(prop.proposal_id)
    sa.request_approval(prop.proposal_id)
    sa.resolve_approval(prop.proposal_id, approved=False)
    prop = sa.install_skill(prop.proposal_id)
    assert prop.status == sa.ProposalStatus.REJECTED
    assert get_agent_tool("need_approval") is None


def test_execution_after_installation():
    sa.acquire_skill_pipeline(
        name="json_check",
        description="Validate JSON payload",
        reason="parse configs",
        requested_by="agent",
        risk="low",
        handler_kind="json_validate",
        input_schema={"type": "object", "properties": {"payload": {"type": "string"}}},
    )
    out = sa.run_dynamic_skill_handler("json_check", {"payload": '{"a":1}'})
    assert out["ok"] is True
    assert out.get("valid") is True
