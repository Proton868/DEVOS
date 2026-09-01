"""
DevOS Autoresearch Mode — adapted from Karpathy's autoresearch philosophy.
═══════════════════════════════════════════════════════════════════════════════
Original autoresearch: modify ML training code → train 5min → measure loss →
keep if better, discard if worse → repeat.

DevOS adaptation (works on ANY task, not just ML training, runs on CPU):
  1. Parse goal into a measurable, binary-checkable target
     (e.g. "make this function faster" → verification command + metric)
  2. Brain proposes ONE change
  3. Execute + measure against the metric
  4. Compare to best-so-far
  5. Keep if better (commit), discard if worse (revert)
  6. Repeat for N rounds or until plateau detected
  7. Full history logged to results.jsonl — nothing is silently lost

Key principles carried over from Karpathy's design:
  - Fixed round budget (not open-ended) — comparable experiments
  - Binary/numeric metrics only — no subjective 1-10 scoring (noisy)
  - Every accepted change must not break existing behavior (regression check)
  - Full experiment history preserved for resume/analysis
  - Self-contained — no extra infra beyond what DevOS already has
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.autoresearch")

RESULTS_DIR = Path("data/autoresearch")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ResearchGoal:
    """Parsed, machine-readable research goal (Karpathy's 7-component decomposition)."""
    raw_goal:        str
    metric:          str             # what we're measuring, e.g. "execution_time_ms"
    direction:       str             # "minimize" | "maximize"
    target_code:     str             # the code/script being improved
    scope:           str             # what can be changed
    verify_command:  Optional[str]   # how to verify correctness (regression check)
    baseline_value:  Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "raw_goal": self.raw_goal, "metric": self.metric,
            "direction": self.direction, "scope": self.scope,
            "verify_command": self.verify_command,
            "baseline_value": self.baseline_value,
        }


@dataclass
class ResearchRound:
    round_num:    int
    code:         str
    metric_value: Optional[float]
    passed_verify: bool
    accepted:     bool
    reasoning:    str
    error:        Optional[str] = None
    duration_ms:  int = 0
    timestamp:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "round": self.round_num,
            "metric_value": self.metric_value,
            "passed_verify": self.passed_verify,
            "accepted": self.accepted,
            "reasoning": self.reasoning,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "code_preview": self.code[:300],
        }


class AutoresearchSession:
    """
    Runs a Karpathy-style iterative improvement loop on a piece of code or task.
    Fixed round budget. Binary accept/discard. Full history logged.
    """

    def __init__(self, user_id: str, max_rounds: int = 12,
                 timeout_per_round_s: int = 30):
        self.user_id = user_id
        self.max_rounds = max_rounds
        self.timeout_per_round_s = timeout_per_round_s
        self.session_id = str(uuid.uuid4())
        self.results_file = RESULTS_DIR / f"{self.session_id}.jsonl"

    async def parse_goal(self, raw_goal: str, current_code: str) -> ResearchGoal:
        """Parse natural language into measurable components (Brain-assisted)."""
        from brain.llm import BrainLLM
        brain = BrainLLM()

        prompt = f"""Decompose this optimization goal into machine-readable components.

GOAL: {raw_goal}

CURRENT CODE:
```
{current_code[:2000]}
```

Respond ONLY with JSON:
{{
  "metric": "what to measure, e.g. execution_time_ms, output_correctness, lines_of_code",
  "direction": "minimize or maximize",
  "scope": "what parts of the code can be changed",
  "verify_command": "a python snippet or assertion that MUST pass for the change to be valid (regression check), or null if not applicable"
}}"""
        try:
            response = await brain.stream_chat([{"role": "user", "content": prompt}])
            text = response.strip()
            for fence in ["```json", "```"]:
                text = text.replace(fence, "")
            parsed = json.loads(text.strip())
        except Exception as e:
            logger.warning(f"Goal parsing failed, using defaults: {e}")
            parsed = {"metric": "execution_time_ms", "direction": "minimize",
                      "scope": "entire function", "verify_command": None}

        return ResearchGoal(
            raw_goal=raw_goal,
            metric=parsed.get("metric", "execution_time_ms"),
            direction=parsed.get("direction", "minimize"),
            target_code=current_code,
            scope=parsed.get("scope", "entire function"),
            verify_command=parsed.get("verify_command"),
        )

    async def run(self, goal: ResearchGoal, on_round=None) -> dict:
        """
        Run the fixed-budget improvement loop.
        Returns: best code found, full history, summary stats.
        """
        from governance.sandbox import SandboxedExecutor
        sandbox = SandboxedExecutor(max_cpu_seconds=self.timeout_per_round_s)

        best_code = goal.target_code
        best_metric = goal.baseline_value
        history: list[ResearchRound] = []
        accepted_count = 0
        consecutive_rejects = 0

        # Measure baseline first
        baseline_metric, baseline_ok = await self._measure(
            sandbox, best_code, goal
        )
        if baseline_metric is not None:
            best_metric = baseline_metric
        self._log_jsonl({
            "round": 0, "type": "baseline",
            "metric_value": baseline_metric, "code_preview": best_code[:200],
        })

        for round_num in range(1, self.max_rounds + 1):
            t0 = time.perf_counter()

            # Generate candidate change via Brain
            candidate_code, reasoning = await self._propose_change(
                best_code, goal, history
            )

            # Measure candidate
            metric_value, passed_verify = await self._measure(
                sandbox, candidate_code, goal
            )

            duration_ms = int((time.perf_counter() - t0) * 1000)

            # Decide: keep or discard
            accepted = False
            error = None

            if metric_value is None:
                error = "Execution failed or metric extraction failed"
            elif not passed_verify and goal.verify_command:
                error = "Failed regression check"
            else:
                if best_metric is None:
                    accepted = True
                elif goal.direction == "minimize" and metric_value < best_metric:
                    accepted = True
                elif goal.direction == "maximize" and metric_value > best_metric:
                    accepted = True

            if accepted:
                best_code = candidate_code
                best_metric = metric_value
                accepted_count += 1
                consecutive_rejects = 0
            else:
                consecutive_rejects += 1

            round_result = ResearchRound(
                round_num=round_num, code=candidate_code,
                metric_value=metric_value, passed_verify=passed_verify,
                accepted=accepted, reasoning=reasoning, error=error,
                duration_ms=duration_ms,
            )
            history.append(round_result)
            self._log_jsonl(round_result.to_dict())

            if on_round:
                try:
                    await on_round(round_result)
                except Exception:
                    pass

            # Plateau detection — stop early if no improvement in last 5 rounds
            if consecutive_rejects >= 5:
                logger.info(f"Autoresearch plateau detected at round {round_num}")
                break

        improvement_pct = self._calc_improvement(baseline_metric, best_metric, goal.direction)

        # Distill a lesson into Memory's Learning division. This is
        # deliberately separate from the full JSONL trace above (_log_jsonl)
        # — that's the complete record for replay/debugging; this is the
        # short, queryable takeaway that a future Evolution pass or another
        # Worker could actually recall without re-reading every round.
        try:
            from memory.store import MemoryStore
            if accepted_count > 0:
                lesson = (f"Goal '{goal.metric}' ({goal.direction}): improved from "
                          f"{baseline_metric} to {best_metric} ({improvement_pct}%) "
                          f"over {len(history)} rounds, {accepted_count} accepted changes.")
            else:
                lesson = (f"Goal '{goal.metric}' ({goal.direction}): no improvement found "
                          f"over {len(history)} rounds — baseline {baseline_metric} stood.")
            await MemoryStore().save_learning(
                user_id=self.user_id, lesson=lesson, source="autoresearch",
                metadata={"session_id": self.session_id, "accepted_count": accepted_count,
                          "rounds_run": len(history), "improvement_pct": improvement_pct},
            )
        except Exception as e:
            logger.warning(f"[autoresearch] failed to write learning-division lesson: {e}")

        return {
            "session_id": self.session_id,
            "goal": goal.to_dict(),
            "best_code": best_code,
            "best_metric": best_metric,
            "baseline_metric": baseline_metric,
            "rounds_run": len(history),
            "accepted_count": accepted_count,
            "improvement_pct": improvement_pct,
            "history": [r.to_dict() for r in history],
        }

    async def _propose_change(self, current_code: str, goal: ResearchGoal,
                              history: list[ResearchRound]) -> tuple[str, str]:
        """Brain proposes ONE modification to improve the metric."""
        from brain.llm import BrainLLM
        brain = BrainLLM()

        recent_failures = [h for h in history[-3:] if not h.accepted]
        failure_context = ""
        if recent_failures:
            failure_context = "\n\nRECENT FAILED ATTEMPTS (avoid repeating these mistakes):\n" + \
                "\n".join(f"- {h.reasoning}: {h.error or 'no improvement'}" for h in recent_failures)

        prompt = f"""You are optimizing code for: {goal.metric} ({goal.direction})

CURRENT BEST CODE:
```python
{current_code}
```

SCOPE: {goal.scope}
{failure_context}

Propose ONE specific, complete code change to improve {goal.metric}.
Return ONLY the full modified code (no markdown fences, no explanation before/after).
The code must be complete and runnable as-is."""

        response = await brain.stream_chat([{"role": "user", "content": prompt}])
        code = response.strip()
        for fence in ["```python", "```"]:
            code = code.replace(fence, "")
        code = code.strip()

        reasoning = f"Round targeting {goal.metric} {goal.direction}"
        return code, reasoning

    async def _measure(self, sandbox, code: str,
                       goal: ResearchGoal) -> tuple[Optional[float], bool]:
        """Execute code and extract the metric value. Returns (value, passed_verify)."""
        # Inject timing wrapper if metric is execution time
        if "time" in goal.metric.lower():
            instrumented = self._add_timing_wrapper(code)
        else:
            instrumented = code

        result = await sandbox.run(instrumented, language="python",
                                    timeout=self.timeout_per_round_s)

        if result.status != "success":
            return None, False

        # Extract metric from stdout (look for __METRIC__: value)
        metric_value = None
        match = re.search(r"__METRIC__:\s*([0-9.eE+-]+)", result.stdout)
        if match:
            try:
                metric_value = float(match.group(1))
            except ValueError:
                pass

        # Run verification if specified
        passed_verify = True
        if goal.verify_command:
            verify_code = f"{code}\n\n{goal.verify_command}\nprint('__VERIFY_OK__')"
            verify_result = await sandbox.run(verify_code, language="python",
                                              timeout=self.timeout_per_round_s)
            passed_verify = "__VERIFY_OK__" in verify_result.stdout

        return metric_value, passed_verify

    def _add_timing_wrapper(self, code: str) -> str:
        """Wrap code with timing instrumentation."""
        return f"""import time as __time
__t0 = __time.perf_counter()

{code}

__elapsed_ms = (__time.perf_counter() - __t0) * 1000
print(f"__METRIC__:{{__elapsed_ms}}")
"""

    def _calc_improvement(self, baseline: Optional[float], best: Optional[float],
                          direction: str) -> Optional[float]:
        if baseline is None or best is None or baseline == 0:
            return None
        if direction == "minimize":
            return round((baseline - best) / baseline * 100, 2)
        else:
            return round((best - baseline) / baseline * 100, 2)

    def _log_jsonl(self, entry: dict):
        with open(self.results_file, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    @staticmethod
    def list_sessions() -> list[dict]:
        """List all past autoresearch sessions."""
        sessions = []
        for f in RESULTS_DIR.glob("*.jsonl"):
            try:
                lines = f.read_text().strip().split("\n")
                if not lines:
                    continue
                last = json.loads(lines[-1])
                sessions.append({
                    "session_id": f.stem,
                    "rounds": len(lines) - 1,  # minus baseline
                    "last_metric": last.get("metric_value"),
                })
            except Exception:
                pass
        return sessions

    @staticmethod
    def load_session(session_id: str) -> list[dict]:
        f = RESULTS_DIR / f"{session_id}.jsonl"
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().strip().split("\n") if line]
