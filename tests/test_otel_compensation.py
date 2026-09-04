import asyncio
import json
from execution.saga_compensation import (
    policy_for, CompensationMode, CompensationRisk, evaluate_conditions, CompensationOutcome,
)
from execution.saga import create_saga, begin_step, complete_step, fail_step, fail_saga, compensate_saga, load_saga
from observability.otel import (
    init_otel, otel_health, sanitize_attributes, parse_traceparent, format_traceparent,
    start_otel_span, get_memory_spans,
)
from observability.tracing import start_span, set_current_trace, get_trace_spans


def test_formal_policy_matrix():
    assert policy_for("preview").mode == CompensationMode.AUTOMATIC
    assert policy_for("preview").action == "STOP_RUNTIME"
    assert policy_for("github_push").mode == CompensationMode.MANUAL
    assert policy_for("github_push").risk_class == CompensationRisk.CRITICAL
    assert policy_for("github_push").requires_hitl is True
    assert policy_for("deploy").mode == CompensationMode.MANUAL
    assert policy_for("create_share").mode == CompensationMode.AUTOMATIC
    assert policy_for("github_branch").mode == CompensationMode.CONDITIONAL
    assert policy_for("inspect").mode == CompensationMode.NONE


def test_conditional_branch_blocks_unrelated_commits():
    pol = policy_for("github_branch")
    ok, why = evaluate_conditions(pol, {
        "type": "branch", "owned_by_saga": True, "has_unrelated_commits": True,
    })
    assert not ok and why == "unrelated_commits"
    ok2, _ = evaluate_conditions(pol, {
        "type": "branch", "owned_by_saga": True, "has_unrelated_commits": False, "protected": False,
    })
    assert ok2


def test_scenario_a_runtime_share_auto_deploy_manual():
    s = create_saga(plan_id="scen-a")
    for action, resource in (
        ("start_runtime", {"type": "runtime", "id": "rt1", "owned_by_saga": True}),
        ("create_share", {"type": "share", "id": "shr1", "owned_by_saga": True}),
        ("deploy", {"type": "deployment", "id": "dep1", "owned_by_saga": True}),
    ):
        st = begin_step(s, node_id=action, action=action, meta={"resource": resource, **resource})
        complete_step(st, meta={"resource": resource, **resource})
    st = begin_step(s, node_id="verify", action="deploy_verify")
    fail_step(st, "verify failed")
    fail_saga(s, "verify failed")
    result = asyncio.run(compensate_saga(s, user_id="u", project_id="p"))
    loaded = load_saga(s.saga_id)
    by_action = {x.action: x for x in loaded.steps}
    assert by_action["start_runtime"].status == "COMPENSATED"
    assert by_action["create_share"].status == "COMPENSATED"
    assert by_action["deploy"].status == "MANUAL_REMEDIATION"


def test_scenario_b_branch_with_unrelated_commits_not_deleted():
    s = create_saga(plan_id="scen-b")
    st = begin_step(s, node_id="br", action="github_branch", meta={
        "resource": {"type": "branch", "name": "devos/x", "owned_by_saga": True, "has_unrelated_commits": True},
    })
    complete_step(st, meta={
        "resource": {"type": "branch", "name": "devos/x", "owned_by_saga": True, "has_unrelated_commits": True},
    })
    result = asyncio.run(compensate_saga(s))
    loaded = load_saga(s.saga_id)
    assert loaded.steps[0].status == "MANUAL_REMEDIATION"


def test_scenario_d_partial_on_manual_mix():
    s = create_saga(plan_id="scen-d")
    st = begin_step(s, node_id="rt", action="preview", meta={"resource": {"type": "runtime", "owned_by_saga": True}})
    complete_step(st, meta={"resource": {"type": "runtime", "owned_by_saga": True}})
    st2 = begin_step(s, node_id="push", action="github_push")
    complete_step(st2)
    result = asyncio.run(compensate_saga(s, user_id="u", project_id="p"))
    assert result["status"] in ("PARTIALLY_COMPENSATED", "MANUAL_REMEDIATION")
    assert result["status"] != "COMPENSATED" or any(
        r.get("status") == "MANUAL_REMEDIATION" for r in result.get("results", [])
    )


def test_otel_init_and_span_without_collector():
    h = init_otel(force_memory=True)
    assert h["otel_sdk_available"] in (True, False)  # may or may not have package in env
    # always works as no-op or real
    with start_otel_span("test.span", kind="internal", attributes={"plan_id": "p", "token": "secret"}):
        pass
    # sanitize
    clean = sanitize_attributes({"plan_id": "x", "api_key": "sk-secret", "Authorization": "Bearer x"})
    assert "api_key" not in clean and "Authorization" not in str(clean)
    assert "devos.plan_id" in clean or "plan_id" in str(clean)


def test_w3c_traceparent_parse():
    assert parse_traceparent(None) is None
    assert parse_traceparent("garbage") is None
    assert parse_traceparent("00-00000000000000000000000000000000-0000000000000000-01") is None
    good = format_traceparent("abcdef0123456789abcdef0123456789", "abcdef0123456789")
    parsed = parse_traceparent(good)
    assert parsed is not None
    assert parsed[0] == "abcdef0123456789abcdef0123456789"


def test_devos_span_still_works_with_otel_bridge():
    set_current_trace(None)
    with start_span("mission", kind="mission", attributes={"plan_id": "otel-bridge"}):
        with start_span("node", kind="dag.node", attributes={"node_id": "x", "token": "nope"}):
            pass
    # durable spans exist
    # find latest by scanning — use a known attribute path via creating new
    from observability.tracing import new_trace
    # just ensure no exception and health
    h = otel_health()
    assert "otel_sdk_available" in h
