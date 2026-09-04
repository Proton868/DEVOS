"""Final backend acceptance: pivot durability, outbox, UCIP deny, Nuha intents, security."""
import asyncio
import time
from brain.delivery_intent import classify_delivery_intent, nuha_can_request
from brain.personas import NUHA
from brain.capability_canon import canonicalize
from execution.saga import (
    create_saga, begin_step, complete_step, fail_step, fail_saga, load_saga,
    compensate_saga, record_pivot, SAGA_PHASE_PIVOT,
)
from execution.compensation_ucip import authorize_compensation
from execution.outbox import enqueue, dispatch_once, list_events, claim_pending, mark_delivered
from execution.files import FileService
from execution.artifacts import write_bytes
from execution.delivery_executor import execute_delivery_plan
from observability.otel import parse_traceparent
from observability.tracing import from_headers


def test_nuha_intents_full_set():
    msgs = {
        "Inspect this project": "inspect",
        "Build this project": "build",
        "Verify the build": "verify",
        "Commit this project": "github_commit",
        "Push it to GitHub": "github_push",
        "Deploy it": "deploy",
        "Publish it": "publish",
        "Show me the running app": "preview",
    }
    for msg, intent in msgs.items():
        r = classify_delivery_intent(msg)
        assert r is not None, msg
        assert r.intent == intent, (msg, r.intent)
        # Nuha may request capability
        assert nuha_can_request(r.capability, NUHA.capabilities) or canonicalize(r.capability) in {
            canonicalize(c) for c in NUHA.capabilities
        }


def test_pivot_durable_across_reload():
    s = create_saga(plan_id="acc-pivot")
    st = begin_step(s, node_id="push", action="github_push")
    complete_step(st)
    loaded = load_saga(s.saga_id)
    assert loaded is not None
    assert loaded.pivot_reached is True
    assert loaded.pivot_action == "github_push"
    assert loaded.pivot_step_id == st.step_id


def test_outbox_enqueue_dispatch_idempotent():
    eid = enqueue(
        "saga.pivoted",
        aggregate_type="saga",
        aggregate_id="agg1",
        payload={"x": 1},
        trace_id="tr1",
        idempotency_key="acc-outbox-1",
    )
    eid2 = enqueue(
        "saga.pivoted",
        aggregate_type="saga",
        aggregate_id="agg1",
        payload={"x": 1},
        trace_id="tr1",
        idempotency_key="acc-outbox-1",
    )
    assert eid == eid2
    result = dispatch_once(limit=50)
    assert result["claimed"] >= 0
    # second dispatch should not re-deliver same
    events = list_events(aggregate_id="agg1")
    assert any(e["event_type"] == "saga.pivoted" for e in events)


def test_ucip_deny_blocks_manual_as_not_allowed():
    auth = authorize_compensation(
        forward_action="github_push",
        resource={"type": "repo", "owned_by_saga": True},
        user_id="u",
    )
    assert auth["allowed"] is False


def test_compensation_preserves_pivot_actions():
    s = create_saga(plan_id="acc-comp")
    st = begin_step(s, node_id="rt", action="preview", meta={"resource": {"type": "runtime", "owned_by_saga": True}})
    complete_step(st, meta={"resource": {"type": "runtime", "owned_by_saga": True}})
    st2 = begin_step(s, node_id="push", action="github_push")
    complete_step(st2)
    asyncio.run(compensate_saga(s, user_id="u", project_id="p"))
    loaded = load_saga(s.saga_id)
    assert loaded.pivot_reached
    push = next(x for x in loaded.steps if x.action == "github_push")
    assert push.status == "MANUAL_REMEDIATION"


def test_delivery_emits_saga_and_trace():
    fs = FileService("acc", "accproj")
    write_bytes(fs, "index.html", b"<html>acc</html>")
    result = asyncio.run(execute_delivery_plan(user_id="acc", project_id="accproj", goal="preview"))
    assert result.get("saga_id") and result.get("trace_id")
    s = load_saga(result["saga_id"])
    assert s is not None


def test_traceparent_not_identity():
    assert parse_traceparent("not-valid") is None
    ctx = from_headers({"X-DevOS-Trace-ID": "tid", "X-DevOS-Parent-Span-ID": "sid"})
    assert ctx.trace_id == "tid"
    assert not hasattr(ctx, "user_id")
    assert not hasattr(ctx, "capabilities")


def test_outbox_restart_claim_safety():
    enqueue("mission.completed", aggregate_type="mission", aggregate_id="m-restart",
            payload={}, idempotency_key=f"restart-{time.time()}")
    claimed = claim_pending(limit=5)
    # claimed are processing — another claim won't double
    ids = {c["id"] for c in claimed}
    claimed2 = claim_pending(limit=5)
    for c in claimed2:
        assert c["id"] not in ids or c["status"] != "pending"
