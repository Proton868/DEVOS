import asyncio
import json
from execution.saga import (
    create_saga, begin_step, complete_step, fail_step, complete_saga, fail_saga,
    load_saga, compensate_saga,
)
from execution.saga_compensation import policy_for
from observability.tracing import (
    start_span, new_trace, set_current_trace, get_trace_spans, propagate_headers,
    from_headers, tracing_health,
)
from execution.files import FileService
from execution.artifacts import write_bytes
from execution.delivery_executor import execute_delivery_plan
from execution.cancel_cascade import request_delivery_cancel, clear_delivery_cancel


def test_compensation_policies():
    assert policy_for("preview").mode == "AUTOMATIC"
    assert policy_for("github_push").mode == "MANUAL"
    assert policy_for("deploy").mode == "MANUAL"
    assert policy_for("inspect").mode == "NONE"


def test_saga_success_and_load():
    s = create_saga(plan_id="p1", mission_id="m1", trace_id="t1")
    st = begin_step(s, node_id="n1", action="inspect", trace_id="t1")
    complete_step(st, evidence_id="ev1")
    complete_saga(s)
    loaded = load_saga(s.saga_id)
    assert loaded is not None
    assert loaded.status == "COMPLETED"
    assert loaded.steps[0].status == "COMPLETED"
    assert loaded.steps[0].evidence_id == "ev1"


def test_saga_automatic_compensation_preview():
    s = create_saga(plan_id="p2")
    st = begin_step(s, node_id="n1", action="preview", meta={"runtime_id": "x"})
    complete_step(st, meta={"runtime_id": "x"})
    st2 = begin_step(s, node_id="n2", action="github_push")
    complete_step(st2)
    result = asyncio.run(compensate_saga(s, user_id="u", project_id="p"))
    assert result["status"] in ("PARTIALLY_COMPENSATED", "MANUAL_REMEDIATION", "COMPENSATED")
    loaded = load_saga(s.saga_id)
    # push must not auto-destroy
    push_step = [x for x in loaded.steps if x.action == "github_push"][0]
    assert push_step.status == "MANUAL_REMEDIATION"
    # idempotent
    result2 = asyncio.run(compensate_saga(s, user_id="u", project_id="p"))
    assert result2.get("idempotent") or result2["status"] in (
        "PARTIALLY_COMPENSATED", "MANUAL_REMEDIATION", "COMPENSATED"
    )


def test_tracing_parent_child_and_no_secrets():
    set_current_trace(None)
    with start_span("mission", kind="mission", attributes={"plan_id": "p", "token": "secret-value"}):
        with start_span("node", kind="dag.node", attributes={"node_id": "build"}):
            ctx = __import__("observability.tracing", fromlist=["get_current_trace"]).get_current_trace()
            assert ctx is not None
            tid = ctx.trace_id
    spans = get_trace_spans(tid)
    assert len(spans) >= 2
    attrs = json.loads(spans[0]["attributes"] or "{}")
    assert "token" not in attrs
    # headers
    h = propagate_headers()
    # after span ends current may be none
    parent = spans[-1]
    h2 = {"X-DevOS-Trace-ID": parent["trace_id"], "X-DevOS-Parent-Span-ID": parent["span_id"]}
    ctx2 = from_headers(h2)
    assert ctx2.trace_id == parent["trace_id"]
    assert ctx2.parent_span_id == parent["span_id"]
    health = tracing_health()
    assert health["tracing_enabled"] is True


def test_failure_scenario_preserves_manual_steps():
    """BUILD ok, GITHUB ok, DEPLOY ok, VERIFY fail — push/deploy stay MANUAL."""
    s = create_saga(plan_id="fail-scen")
    for action in ("build", "github_commit", "github_push", "deploy"):
        st = begin_step(s, node_id=action, action=action)
        complete_step(st)
    st = begin_step(s, node_id="verify", action="deploy_verify")
    fail_step(st, "VERIFY_FAILED")
    fail_saga(s, "verify failed")
    result = asyncio.run(compensate_saga(s))
    loaded = load_saga(s.saga_id)
    for action in ("github_push", "deploy", "github_commit"):
        step = next(x for x in loaded.steps if x.action == action)
        assert step.status == "MANUAL_REMEDIATION", f"{action} was {step.status}"


def test_delivery_returns_saga_and_trace():
    fs = FileService("su", "sp")
    write_bytes(fs, "index.html", b"<html>s</html>")
    result = asyncio.run(execute_delivery_plan(user_id="su", project_id="sp", goal="preview"))
    assert result.get("saga_id")
    assert result.get("trace_id")
    loaded = load_saga(result["saga_id"])
    assert loaded is not None
    spans = get_trace_spans(result["trace_id"])
    assert len(spans) >= 1


def test_cancel_triggers_compensation_path():
    fs = FileService("cu2", "cp2")
    write_bytes(fs, "index.html", b"<html>c</html>")
    plan_id = "saga-cancel-1"
    request_delivery_cancel(plan_id)
    result = asyncio.run(execute_delivery_plan(
        user_id="cu2", project_id="cp2", goal="preview", plan_id=plan_id,
    ))
    assert result["status"] == "cancelled" or any(
        e.get("status") == "cancelled" for e in result["evidence"]
    )
    clear_delivery_cancel(plan_id)
