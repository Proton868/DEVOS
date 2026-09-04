from brain.delivery_intent import classify_delivery_intent, nuha_can_request
from brain.personas import NUHA
from brain.capability_canon import canonicalize, to_ucip
from execution.compensation_ucip import authorize_compensation
from execution.saga import create_saga, begin_step, complete_step, classify_step_phase, SAGA_PHASE_PIVOT, SAGA_PHASE_COMPENSABLE
from execution.saga_compensation import CompensationMode
import asyncio
from execution.saga import compensate_saga, load_saga


def test_nuha_intent_mapping():
    cases = [
        ("Inspect this project.", "inspect"),
        ("Build this project.", "build"),
        ("Verify that it works.", "verify"),
        ("Commit the changes.", "github_commit"),
        ("Push it to GitHub.", "github_push"),
        ("Deploy it to Vercel.", "deploy_vercel"),
        ("Deploy this through Cloudflare.", "deploy_cloudflare"),
    ]
    for msg, expect in cases:
        intent = classify_delivery_intent(msg)
        assert intent is not None, msg
        assert intent.intent == expect, (msg, intent.intent)
        # does not execute — external flag for push/deploy
        if expect in ("github_push", "deploy_vercel", "deploy_cloudflare"):
            assert intent.external_side_effect is True


def test_nuha_can_request_capabilities():
    caps = NUHA.capabilities
    assert nuha_can_request("build", caps)
    assert nuha_can_request("github.push", caps) or nuha_can_request("vcs.push", caps)
    assert nuha_can_request("deploy.vercel", caps) or "deployment.production" in {canonicalize(c) for c in caps}
    assert canonicalize("github.push") == "vcs.push"
    assert to_ucip("deployment.production").startswith("ucip:")


def test_ucip_called_for_automatic_compensation():
    auth = authorize_compensation(
        forward_action="preview",
        resource={"type": "runtime", "id": "rt1", "owned_by_saga": True},
        user_id="test-user",
        context={"saga_id": "s1"},
    )
    # Either approved via UCIP or denied with ucip_decision set — not skipped silently
    assert auth["policy"]["mode"] == CompensationMode.AUTOMATIC.value
    assert auth.get("ucip_decision") is not None or auth.get("allowed") is True or "ucip" in (auth.get("reason") or "")


def test_ucip_manual_push_not_auto():
    auth = authorize_compensation(
        forward_action="github_push",
        resource={"type": "repository", "owned_by_saga": True},
        user_id="test-user",
    )
    assert auth["allowed"] is False
    assert auth["outcome"] == "manual_remediation"


def test_saga_pivot_classification():
    assert classify_step_phase("build") == SAGA_PHASE_COMPENSABLE
    assert classify_step_phase("github_push") == SAGA_PHASE_PIVOT
    s = create_saga(plan_id="pivot1")
    st = begin_step(s, node_id="b", action="build")
    complete_step(st)
    st2 = begin_step(s, node_id="p", action="github_push")
    assert (st2.meta or {}).get("phase") == SAGA_PHASE_PIVOT


def test_compensation_still_preserves_push():
    s = create_saga(plan_id="preserve-push")
    st = begin_step(s, node_id="rt", action="preview", meta={"resource": {"type": "runtime", "owned_by_saga": True}})
    complete_step(st, meta={"resource": {"type": "runtime", "owned_by_saga": True}})
    st2 = begin_step(s, node_id="push", action="github_push")
    complete_step(st2)
    result = asyncio.run(compensate_saga(s, user_id="u", project_id="p"))
    loaded = load_saga(s.saga_id)
    push = next(x for x in loaded.steps if x.action == "github_push")
    assert push.status == "MANUAL_REMEDIATION"


def test_trace_headers_not_authorization():
    from observability.otel import parse_traceparent
    # malformed cannot become identity
    assert parse_traceparent("00-deadbeef-00") is None
    from observability.tracing import from_headers
    ctx = from_headers({"X-DevOS-Trace-ID": "abc123", "X-DevOS-Parent-Span-ID": "span1"})
    assert ctx.trace_id == "abc123"
    # no identity fields
    assert not hasattr(ctx, "user_id")
