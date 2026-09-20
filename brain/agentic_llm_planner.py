"""Bounded LLM planner for the agentic runtime.

The LLM is an untrusted planning component only. It does not possess
execution authority. Flow:

  LLM planner → StructuredPlan → validation → Agent Runtime → UCIP → …
  → Execution → Evidence → CompletionContract

No subprocess, HTTP, filesystem, or grant manufacturing from this module.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from brain.agentic_runtime import TurnDecision, _bound_str, _scrub_context

logger = logging.getLogger("devos.agentic_llm_planner")

ALLOWED_ACTION_TYPES = frozenset({
    "capability_request",
    "observe",
    "complete",
    "wait",
    "block",
    "fail",
})

# Planner must never request these as capability ids (execution authority vectors)
FORBIDDEN_CAPABILITY_PREFIXES = (
    "shell:", "subprocess:", "http:", "https:", "file:", "fs:",
    "python:", "exec:", "eval:", "grant:", "auth:", "secret:",
)

FORBIDDEN_PLAN_KEYS = frozenset({
    "owner_id", "tenant_id", "user_id", "grants", "granted_capabilities",
    "isolation", "isolation_strength", "retry", "retry_policy",
    "max_turns", "max_capabilities", "timeout", "timeout_s",
    "operation_id", "job_id", "evidence", "evidence_refs",
    "authorization", "bypass_ucip", "bypass_isolation",
})

FORBIDDEN_INPUT_KEYS = frozenset({

    "grant", "grants", "authorization", "authority", "token", "api_key",
    "password", "secret", "credentials", "service_role", "jwt",
})

MAX_PLANNER_CONTEXT_CHARS = 12_000
MAX_RATIONALE_CHARS = 500
MAX_INPUT_JSON_CHARS = 4_000


class PlannerValidationError(ValueError):
    """Malformed or disallowed planner output — no execution."""


@dataclass
class StructuredPlan:
    action_type: str
    capability: Optional[str] = None
    input: dict = field(default_factory=dict)
    rationale: str = ""

    def to_turn_decision(self) -> TurnDecision:
        kind = self.action_type
        if kind == "capability_request":
            return TurnDecision(
                kind="capability_request",
                capability_id=self.capability,
                inputs=dict(self.input or {}),
                reason=self.rationale or "llm_capability_request",
            )
        if kind == "complete":
            return TurnDecision(
                kind="complete",
                complete=True,
                reason=self.rationale or "llm_structured_complete",
            )
        if kind in ("block", "fail", "wait", "observe"):
            return TurnDecision(
                kind="block" if kind in ("wait", "observe", "block") else "fail",
                reason=self.rationale or f"llm_{kind}",
            )
        raise PlannerValidationError(f"unmapped_action:{kind}")


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_structured_plan(raw: Any) -> StructuredPlan:
    """Parse untrusted model output into StructuredPlan. Raises on invalid."""
    if raw is None:
        raise PlannerValidationError("empty_plan")
    if isinstance(raw, StructuredPlan):
        return raw
    if isinstance(raw, TurnDecision):
        return StructuredPlan(
            action_type=raw.kind,
            capability=raw.capability_id,
            input=dict(raw.inputs or {}),
            rationale=raw.reason or "",
        )
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        s = _strip_code_fences(raw)
        # Natural language "done" is NOT a plan
        low = s.lower().strip()
        if low in ("done", "finished", "success", "completed", "complete"):
            raise PlannerValidationError("free_form_complete_rejected")
        data = None
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            # Extract first JSON object from noisy model prose
            start = s.find("{")
            end = s.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(s[start : end + 1])
                except json.JSONDecodeError as e:
                    raise PlannerValidationError(f"malformed_json:{e}") from e
            else:
                raise PlannerValidationError("malformed_json:no_object")
        if data is None:
            raise PlannerValidationError("malformed_json:empty")
    else:
        raise PlannerValidationError(f"unsupported_plan_type:{type(raw).__name__}")

    if not isinstance(data, dict):
        raise PlannerValidationError("plan_not_object")

    # Nested { "plan": { "action": ... } } or flat
    if "plan" in data and isinstance(data["plan"], dict):
        data = data["plan"]
    action = data.get("action") if isinstance(data.get("action"), dict) else data
    if not isinstance(action, dict):
        raise PlannerValidationError("action_not_object")

    action_type = str(
        action.get("type") or action.get("kind") or data.get("kind") or ""
    ).strip().lower()
    if not action_type:
        raise PlannerValidationError("missing_action_type")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise PlannerValidationError(f"unknown_action:{action_type}")

    # Security-sensitive fields on the plan object are rejected (not ignored)
    for src in (data, action):
        if not isinstance(src, dict):
            continue
        for k in src.keys():
            if str(k).lower() in FORBIDDEN_PLAN_KEYS:
                raise PlannerValidationError(f"forbidden_plan_key:{k}")

    # Reject shell/code as action
    for bad in ("shell", "subprocess", "exec", "eval", "http", "fetch"):
        if bad in action_type:
            raise PlannerValidationError(f"forbidden_action:{action_type}")

    capability = action.get("capability") or action.get("capability_id") or data.get("capability_id")
    if capability is not None:
        capability = str(capability).strip()
        if not capability:
            capability = None
        else:
            low = capability.lower()
            if any(low.startswith(p) for p in FORBIDDEN_CAPABILITY_PREFIXES):
                raise PlannerValidationError(f"forbidden_capability:{capability}")
            if low in ("shell", "bash", "sh", "python", "eval", "exec"):
                raise PlannerValidationError(f"forbidden_capability:{capability}")

    raw_input = action.get("input") if "input" in action else action.get("inputs", data.get("inputs"))
    if raw_input is None:
        inputs: dict = {}
    elif isinstance(raw_input, dict):
        inputs = dict(raw_input)
    else:
        raise PlannerValidationError("input_not_object")

    for k in list(inputs.keys()):
        if str(k).lower() in FORBIDDEN_INPUT_KEYS:
            raise PlannerValidationError(f"forbidden_input_key:{k}")

    inp_s = json.dumps(inputs, default=str)
    if len(inp_s) > MAX_INPUT_JSON_CHARS:
        raise PlannerValidationError("input_too_large")

    if action_type == "capability_request" and not capability:
        raise PlannerValidationError("capability_required")

    rationale = _bound_str(
        action.get("rationale") or data.get("rationale") or data.get("reason") or "",
        MAX_RATIONALE_CHARS,
    )

    return StructuredPlan(
        action_type=action_type,
        capability=capability,
        input=inputs,
        rationale=str(rationale or ""),
    )


def validate_plan_against_context(plan: StructuredPlan, context: dict) -> StructuredPlan:
    """Reject unknown capabilities relative to allowed list when provided."""
    allowed = list(context.get("allowed_capabilities") or [])
    if plan.action_type == "capability_request" and allowed:
        if plan.capability not in allowed:
            raise PlannerValidationError(f"unknown_capability:{plan.capability}")
    # Planner cannot raise bounds
    if plan.input.get("max_turns") or plan.input.get("max_caps"):
        raise PlannerValidationError("cannot_override_bounds")
    return plan


def build_planner_context(context: dict) -> dict:
    """Bounded, scrubbed context for the LLM. Deterministic key order."""
    scrubbed = _scrub_context(dict(context or {}))
    allowed_keys = (
        "task_id", "objective", "state", "turn", "max_turns",
        "allowed_capabilities", "last_observation", "evidence_refs",
        "pending_request", "operation_id", "job_id", "plan",
        "completion_contract", "failure", "cancel_requested",
        "capability_request_count",
    )
    out = {k: scrubbed[k] for k in allowed_keys if k in scrubbed}
    # Never pass grants/secrets
    for banned in ("grants", "secrets", "credentials", "env", "authorization"):
        out.pop(banned, None)
    s = json.dumps(out, default=str, sort_keys=True)
    if len(s) > MAX_PLANNER_CONTEXT_CHARS:
        out = {
            "task_id": out.get("task_id"),
            "objective": _bound_str(out.get("objective"), 500),
            "state": out.get("state"),
            "turn": out.get("turn"),
            "allowed_capabilities": (out.get("allowed_capabilities") or [])[:20],
            "last_observation": out.get("last_observation"),
            "truncated": True,
        }
    return out


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str:
        ...


@dataclass
class FakeLLMProvider:
    """Deterministic provider for tests. No network."""
    script: list[str] = field(default_factory=list)
    _i: int = 0
    raise_exc: Optional[BaseException] = None

    def complete(self, system: str, user: str) -> str:
        if self.raise_exc:
            raise self.raise_exc
        if not self.script:
            return json.dumps({
                "action": {"type": "complete", "rationale": "fake_empty_script"},
            })
        idx = min(self._i, len(self.script) - 1)
        self._i += 1
        return self.script[idx]



def run_local_planner_smoke() -> dict:
    """Always-on smoke: FakeLLM → structured plan → validation → TurnDecision.

    No network. Safe for CI. Proves planner path without external models.
    """
    prov = FakeLLMProvider(script=[
        json.dumps({
            "action": {
                "type": "capability_request",
                "capability": "devos.capability.list",
                "input": {},
                "rationale": "local_smoke",
            }
        }),
        json.dumps({"action": {"type": "complete", "rationale": "local_smoke_done"}}),
    ])
    planner = make_llm_planner(prov)
    ctx = {
        "task_id": "smoke",
        "objective": "list capabilities",
        "allowed_capabilities": ["devos.capability.list"],
        "turn": 0,
    }
    d1 = planner(ctx)
    d2 = planner({**ctx, "turn": 1, "last_observation": {"status": "executed"}})
    return {
        "ok": d1.kind == "capability_request" and d2.kind == "complete",
        "first": d1.to_dict(),
        "second": d2.to_dict(),
        "network": False,
    }


@dataclass
class BrainLLMProvider:
    """Adapter over BrainLLM — network only when explicitly constructed/used."""
    provider: Optional[str] = None
    model: Optional[str] = None

    def complete(self, system: str, user: str) -> str:
        # Lazy import — CI must not require provider
        from brain.llm import BrainLLM
        import asyncio

        brain = BrainLLM(provider=self.provider, model=self.model)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        async def _run() -> str:
            return await brain.stream_chat(messages, allow_fallback=True)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Nested: create task is not sync; use concurrent helper
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(_run())).result(timeout=120)
            return loop.run_until_complete(_run())
        except RuntimeError:
            return asyncio.run(_run())


PLANNER_SYSTEM = """You are a DevOS agentic planner. Reply with JSON only.
Schema:
{"action":{"type":"capability_request|observe|complete|wait|block|fail",
 "capability":"<slug if capability_request>","input":{},"rationale":"..."}}
Rules:
- You have NO execution authority.
- You may only request capabilities from allowed_capabilities.
- Never request shell, HTTP, filesystem, secrets, or grants.
- Never claim completion without type=complete in JSON.
- Free-form text is rejected.
"""


def classify_provider_error(exc: BaseException) -> str:
    """Map provider failures to stable reason codes (no execution)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "planner_timeout"
    if "rate" in msg or "429" in msg:
        return "planner_rate_limited"
    if "connection" in name or "connect" in msg or "network" in msg:
        return "planner_connection_error"
    if "context" in msg and ("large" in msg or "length" in msg or "token" in msg):
        return "planner_context_too_large"
    if "empty" in msg:
        return "planner_empty_response"
    return f"planner_provider_error:{type(exc).__name__}"


def make_llm_planner(
    provider: LLMProvider,
    *,
    on_invalid: str = "block",
    fallback: Optional[Callable[[dict], TurnDecision]] = None,
    fallback_on_provider_error: bool = False,
    planner_meta: Optional[dict] = None,
) -> Callable[[dict], TurnDecision]:
    """Return a PlannerFn that uses the provider. Output is untrusted.

    Parameters
    ----------
    on_invalid:
        "block" | "fail" when structured validation fails.
    fallback:
        Optional deterministic planner used only when fallback_on_provider_error
        is True and the provider raises. Validation failures never execute
        capabilities via fallback unless the fallback itself is invoked
        explicitly by policy.
    fallback_on_provider_error:
        When True and provider.complete raises, call fallback(context) if set;
        otherwise return block/fail. Never auto-executes capabilities.
    planner_meta:
        Non-secret metadata (planner_type, provider, model) attached to the
        decision reason for durability — not authority.
    """
    meta = dict(planner_meta or {})
    meta.setdefault("planner_type", "llm")

    def _annotate(decision: TurnDecision) -> TurnDecision:
        # Bound metadata into reason only — never authority fields
        tag = meta.get("planner_type") or "llm"
        if meta.get("provider"):
            tag = f"{tag}:{meta.get('provider')}"
        if meta.get("model"):
            tag = f"{tag}:{meta.get('model')}"
        if decision.reason:
            decision.reason = _bound_str(f"{decision.reason}|planner={tag}", 500)
        else:
            decision.reason = _bound_str(f"planner={tag}", 500)
        return decision

    def planner(context: dict) -> TurnDecision:
        ctx = build_planner_context(context)
        # Enforce context bound again at adapter boundary
        raw_ctx = json.dumps(ctx, default=str, sort_keys=True)
        if len(raw_ctx) > MAX_PLANNER_CONTEXT_CHARS:
            return _annotate(TurnDecision(
                kind="block",
                reason="planner_context_too_large",
            ))
        try:
            raw = provider.complete(PLANNER_SYSTEM, raw_ctx)
        except Exception as e:
            code = classify_provider_error(e)
            logger.warning("planner_provider_failed: %s", code)
            if fallback_on_provider_error and fallback is not None:
                logger.info("planner_fallback_to_deterministic policy=provider_error")
                try:
                    return _annotate(fallback(context))
                except Exception as fe:
                    return _annotate(TurnDecision(
                        kind="fail",
                        reason=f"planner_fallback_error:{type(fe).__name__}",
                    ))
            kind = "fail" if on_invalid == "fail" else "block"
            return _annotate(TurnDecision(kind=kind, reason=code))

        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return _annotate(TurnDecision(
                kind="block",
                reason="planner_empty_response",
            ))
        if isinstance(raw, str) and len(raw) > MAX_INPUT_JSON_CHARS * 4:
            return _annotate(TurnDecision(
                kind="block",
                reason="planner_output_too_large",
            ))
        try:
            plan = parse_structured_plan(raw)
            plan = validate_plan_against_context(plan, context)
            return _annotate(plan.to_turn_decision())
        except PlannerValidationError as e:
            logger.info("planner_validation_failed: %s", e)
            if on_invalid == "fail":
                return _annotate(TurnDecision(kind="fail", reason=f"planner_invalid:{e}"))
            return _annotate(TurnDecision(kind="block", reason=f"planner_invalid:{e}"))

    return planner


def planner_has_no_execution_authority() -> bool:
    """Architecture guard used by tests.

    Fails if this module contains direct execution imports/calls.
    Uses split tokens so the guard source itself does not self-match.
    """
    import pathlib
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    # Drop this function body from scan
    marker = "def planner_has_no_execution_authority"
    i = src.rfind(marker)
    head = src[:i] if i >= 0 else src
    needles = [
        "sub" + "process.Popen",
        "sub" + "process.run",
        "os.sys" + "tem(",
        "sock" + "et.socket(",
        "File" + "Service(",
        "create_" + "grant(",
    ]
    return not any(n in head for n in needles)

