"""
Cognitive System — Reflection.

Session 3 added expected_outcome_schema validation to mark_complete: a
*structural* check (does the output have the right shape?). This module
adds the piece that was still missing: a *qualitative* check (does the
output actually satisfy what was asked, even if its shape is fine?). A
perfectly-shaped JSON answer can still be wrong, incomplete, or off-topic —
schema validation can't catch that, only genuine critique can.

Deliberately NOT gating completion by default. Reflection is opt-in
(BrainExecutionLoop.run(..., use_reflection=True)) because it costs an
extra LLM call on every completion and, unlike schema validation, its
"failure" verdict is a judgment call rather than a hard structural fact —
forcing every task through a second, possibly-wrong LLM's opinion of a
first LLM's work is a real behavior change that callers should opt into
deliberately, not receive as a silent default.
"""
import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("devos.cognitive.reflector")

REFLECT_PROMPT_TEMPLATE = """Critique this completed task honestly.

GOAL: {goal}

FINAL ANSWER PRODUCED:
{answer}

Does the final answer actually satisfy the goal? Consider: is anything the
goal asked for missing, incorrect, or only partially addressed? Be
skeptical — a plausible-looking answer can still miss the actual ask.

Respond ONLY with JSON, no markdown fences:
{{"satisfied": true/false, "issues": ["...", ...], "confidence": 0.0-1.0}}"""


@dataclass
class Reflection:
    satisfied: bool
    issues: list[str] = field(default_factory=list)
    confidence: float = 0.5
    raw: str = ""
    parse_failed: bool = False

    def to_dict(self) -> dict:
        return {"satisfied": self.satisfied, "issues": self.issues,
                "confidence": self.confidence, "parse_failed": self.parse_failed}


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for fence in ["```json", "```"]:
        if fence in raw:
            try:
                start = raw.index(fence) + len(fence)
                end = raw.index("```", start)
                return json.loads(raw[start:end].strip())
            except (ValueError, json.JSONDecodeError):
                pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("no JSON found")


class Reflector:
    async def reflect(self, goal: str, final_answer: str, brain) -> Reflection:
        """brain: a BrainLLM instance (dependency-injected, same pattern as
        GoalDecomposer, for the same testability reason)."""
        prompt = REFLECT_PROMPT_TEMPLATE.format(goal=goal, answer=final_answer[:4000])
        try:
            raw = await brain._call(brain.provider, [{"role": "user", "content": prompt}])
            parsed = _extract_json(raw)
            return Reflection(
                satisfied=bool(parsed.get("satisfied", True)),
                issues=list(parsed.get("issues", [])),
                confidence=float(parsed.get("confidence", 0.5)),
                raw=raw,
            )
        except Exception as e:
            # A reflection that fails to parse should NOT block task
            # completion the way a failed expected_outcome_schema check
            # does — that would let a flaky critique step deadlock
            # otherwise-fine work. Defaults to satisfied=True with the
            # failure visible, not hidden.
            logger.warning(f"[reflector] could not parse reflection, defaulting to satisfied=True: {e}")
            return Reflection(satisfied=True, issues=[f"reflection step failed to parse: {e}"],
                              confidence=0.0, parse_failed=True)
