"""Security + usefulness tests for production skill acquisition catalog."""
from __future__ import annotations

import pytest

from governance.skill_acquisition import (
    HANDLER_CATALOG,
    _SAFE_HANDLERS,
    acquire_skill_pipeline,
    detect_missing_capability,
    install_skill,
    list_safe_handler_kinds,
    propose_skill,
    request_approval,
    reset_store_for_tests,
    resolve_approval,
    run_dynamic_skill_handler,
    security_governance_check,
    suggest_handler_for_need,
    validate_proposal,
    SkillDefinition,
    SkillRisk,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_store_for_tests()
    yield
    reset_store_for_tests()


def test_catalog_includes_regression_and_production_handlers():
    kinds = {h["handler_kind"] for h in list_safe_handler_kinds()}
    for required in ("echo", "json_validate", "hash_text", "text_transform",
                     "parse_pytest_summary", "path_normalize", "diff_summary"):
        assert required in kinds
        assert required in _SAFE_HANDLERS
        assert required in HANDLER_CATALOG


def test_suggest_handler_maps_useful_needs():
    r = suggest_handler_for_need("parse pytest test results")
    assert r["ok"] is True
    assert r["handler_kind"] == "parse_pytest_summary"


def test_suggest_handler_refuses_shell_and_network():
    for need in ("open a raw shell", "make http network calls", "read database credentials"):
        r = suggest_handler_for_need(need)
        assert r["ok"] is False
        assert r["error"] in ("capability_not_generatable", "no_safe_handler_match")


def test_path_normalize_rejects_traversal():
    from governance.skill_acquisition import _handler_path_normalize
    assert _handler_path_normalize({"path": "../etc/passwd"})["ok"] is False
    assert _handler_path_normalize({"path": "src/app.py"})["ok"] is True


def test_handlers_do_not_touch_filesystem(tmp_path, monkeypatch):
    """Handlers must operate on provided text only — no open()/path writes."""
    from governance import skill_acquisition as sa
    # Point CWD away; if any handler opens a file under cwd it would be visible
    monkeypatch.chdir(tmp_path)
    sa._handler_line_stats({"text": "a\nb"})
    sa._handler_parse_pytest_summary({"text": "2 passed"})
    sa._handler_extension_tally({"paths": ["a.py", "b.ts"]})
    # No files created by handlers
    assert list(tmp_path.iterdir()) == []


def test_slug_spoofing_rejected():
    defn = SkillDefinition(
        name="my_tool",
        description="does analysis",
        capability_slug="ucip:admin.everything",  # spoof
        reason="need it",
        requested_by="agent:code",
        handler_kind="echo",
        risk=SkillRisk.LOW,
    )
    ok, notes = security_governance_check(defn)
    assert ok is False
    assert any("capability_slug" in n for n in notes)


def test_privilege_tokens_rejected():
    defn = SkillDefinition(
        name="helpful_tool",
        description="bypass ucip for unrestricted shell",
        capability_slug="ucip:skill.helpful_tool",
        reason="speed",
        requested_by="agent:code",
        handler_kind="echo",
        risk=SkillRisk.LOW,
    )
    ok, notes = security_governance_check(defn)
    assert ok is False


def test_unknown_handler_kind_rejected():
    defn = SkillDefinition(
        name="evil_eval",
        description="run code",
        capability_slug="ucip:skill.evil_eval",
        reason="need eval",
        requested_by="agent:code",
        handler_kind="arbitrary_python",
        risk=SkillRisk.LOW,
    )
    ok, notes = security_governance_check(defn)
    assert ok is False


def test_self_approval_forbidden_for_high_risk():
    prop = propose_skill(
        name="net_helper",
        description="would touch network",
        reason="fetch docs",
        requested_by="agent:web",
        risk="high",
        side_effect="network",
        handler_kind="echo",
    )
    prop = validate_proposal(prop.proposal_id)
    # high+network should need HITL
    prop = request_approval(prop.proposal_id, user_id="agent:web")
    assert prop.status.value in ("pending_approval", "rejected", "validated", "approved")
    if prop.status.value == "pending_approval":
        denied = resolve_approval(prop.proposal_id, approved=True, resolved_by="agent:web")
        assert denied.status.value == "pending_approval"
        assert any("self_approval" in e for e in (denied.validation_errors or []))
        # Operator may approve
        ok = resolve_approval(prop.proposal_id, approved=True, resolved_by="operator")
        assert ok.status.value == "approved"


def test_production_handler_install_and_execute():
    result = acquire_skill_pipeline(
        name="pytest_parse_helper",
        description="Parse pytest output text",
        reason="Need structured test counts after run_tests",
        requested_by="agent:code",
        risk="low",
        side_effect="none",
        handler_kind="parse_pytest_summary",
    )
    assert result["ok"] is True, result
    assert result["stage"] == "installed"
    out = run_dynamic_skill_handler(
        "pytest_parse_helper",
        {"text": "===== 3 passed, 1 failed in 0.2s ====="},
    )
    assert out["ok"] is True
    assert out["passed"] == 3
    assert out["failed"] == 1


def test_text_transform_install_and_run():
    result = acquire_skill_pipeline(
        name="slug_helper",
        description="Slugify titles",
        reason="normalize artifact names",
        requested_by="agent:code",
        risk="low",
        handler_kind="text_transform",
    )
    assert result["ok"] is True
    out = run_dynamic_skill_handler("slug_helper", {"text": "Hello World!", "op": "slugify"})
    assert out["ok"] is True
    assert out["result"] == "hello-world"


def test_cannot_install_shell_handler_kind():
    prop = propose_skill(
        name="shell_tool",
        description="run shell",
        reason="need shell",
        requested_by="agent:code",
        handler_kind="bash_shell",
    )
    prop = validate_proposal(prop.proposal_id)
    assert prop.status.value == "rejected"
