"""
Cognitive System — Ponytail Pipeline (Agency OS Master Plan §4).

The 11-stage coding pipeline based on the Ponytail philosophy:
"think like the laziest senior dev in the room — the best code is the code
you never wrote." Every non-trivial change ships with exactly one runnable
self-check, and safety-critical code (validation, auth, error handling) is
never cut for brevity.

Pipeline stages:
  Architect → Planner → Engineer → Simplifier → Reviewer → Security →
  Tester → Chaos Test → Fix → Retest → Deploy → Learn

The Simplifier stage is the Ponytail-pattern: rule-injection + mandatory
self-check artifact. The Learning stage closes the loop: lessons from
accepted/rejected changes are stored in Memory:Learning for future runs.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("devos.cognitive.ponytail")


class PipelineStage(str, Enum):
    ARCHITECT  = "architect"   # Design the solution
    PLANNER    = "planner"     # Break into subtasks
    ENGINEER   = "engineer"    # Write the code
    SIMPLIFIER = "simplifier"  # Ponytail: cut unnecessary code, add self-check
    REVIEWER   = "reviewer"    # Code review
    SECURITY   = "security"    # Security audit
    TESTER     = "tester"      # Write and run tests
    CHAOS      = "chaos"       # Chaos/fuzz testing
    FIX        = "fix"         # Fix issues found
    RETEST     = "retest"      # Final verification
    DEPLOY     = "deploy"      # Deploy to target
    LEARN      = "learn"       # Extract lessons


STAGE_DESCRIPTIONS = {
    PipelineStage.ARCHITECT: "Design the architecture — what components, data flow, interfaces",
    PipelineStage.PLANNER: "Break into ordered subtasks with dependencies",
    PipelineStage.ENGINEER: "Write the implementation code",
    PipelineStage.SIMPLIFIER: "Ponytail: remove unnecessary code, ensure one self-check per change",
    PipelineStage.REVIEWER: "Review for correctness, style, best practices",
    PipelineStage.SECURITY: "Audit for vulnerabilities, injection, auth issues",
    PipelineStage.TESTER: "Write and run tests — unit, integration, edge cases",
    PipelineStage.CHAOS: "Fuzz testing, edge cases, failure modes",
    PipelineStage.FIX: "Fix all issues found in review/security/testing",
    PipelineStage.RETEST: "Final verification — all tests pass, no regressions",
    PipelineStage.DEPLOY: "Deploy to target environment",
    PipelineStage.LEARN: "Extract lessons from this run for future improvement",
}

# Ponytail rules injected into the Simplifier stage
PONYTAIL_RULES = """PONYTAIL RULES (enforced at Simplifier stage):
1. Every non-trivial change MUST ship with exactly one runnable self-check.
2. The self-check must be a concrete command that produces pass/fail output.
3. Safety-critical code (validation, auth, error handling) is NEVER cut for brevity.
4. If a change can be expressed as a config change instead of code, prefer config.
5. If a library already does it, use the library — don't reinvent.
6. Remove dead code, unused imports, and commented-out blocks.
7. The best code is the code you never wrote — delete anything unnecessary."""


@dataclass
class StageResult:
    """Output of one pipeline stage."""
    stage:       PipelineStage
    status:      str = "pending"  # pending | running | done | failed | skipped
    output:      str = ""
    artifacts:   list[str] = field(default_factory=list)  # file paths, test results
    errors:      list[str] = field(default_factory=list)
    self_check:  Optional[str] = None  # the one mandatory self-check
    started_at:  Optional[datetime] = None
    finished_at: Optional[datetime] = None
    tokens_used: int = 0

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "status": self.status,
            "output": self.output[:500],
            "artifacts": self.artifacts,
            "errors": self.errors,
            "self_check": self.self_check,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "tokens_used": self.tokens_used,
        }


@dataclass
class PipelineRun:
    """A complete pipeline run for one coding task."""
    run_id:      str
    goal:        str
    stages:      list[StageResult] = field(default_factory=list)
    lessons:     list[str] = field(default_factory=list)
    final_status: str = "pending"
    total_tokens: int = 0
    total_time_ms: int = 0
    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "stages": [s.to_dict() for s in self.stages],
            "lessons": self.lessons,
            "final_status": self.final_status,
            "total_tokens": self.total_tokens,
            "total_time_ms": self.total_time_ms,
            "created_at": self.created_at.isoformat(),
        }


class PonytailPipeline:
    """The 11-stage Ponytail coding pipeline. Each stage is a separate LLM
    call with stage-specific prompts and the Ponytail rules injected at the
    Simplifier stage."""

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider
        self.model = model

    async def run(self, goal: str, code_context: str = "",
                  run_id: Optional[str] = None) -> PipelineRun:
        """Run the full pipeline on a coding goal."""
        import uuid
        run = PipelineRun(
            run_id=run_id or str(uuid.uuid4()),
            goal=goal,
        )

        stages = [
            PipelineStage.ARCHITECT,
            PipelineStage.PLANNER,
            PipelineStage.ENGINEER,
            PipelineStage.SIMPLIFIER,
            PipelineStage.REVIEWER,
            PipelineStage.SECURITY,
            PipelineStage.TESTER,
            PipelineStage.CHAOS,
            PipelineStage.FIX,
            PipelineStage.RETEST,
            PipelineStage.DEPLOY,
            PipelineStage.LEARN,
        ]

        try:
            for stage in stages:
                result = await self._run_stage(stage, goal, code_context, run)
                run.stages.append(result)
                if result.status == "failed":
                    run.final_status = "failed"
                    break
        except Exception as e:
            logger.error(f"[ponytail] pipeline failed: {e}")
            run.final_status = "error"

        if run.final_status == "pending":
            run.final_status = "done"

        # Extract lessons
        learn_stage = next((s for s in run.stages if s.stage == PipelineStage.LEARN), None)
        if learn_stage and learn_stage.output:
            run.lessons = [l.strip() for l in learn_stage.output.split("\n") if l.strip()]

        return run

    async def _run_stage(self, stage: PipelineStage, goal: str,
                         code_context: str, run: PipelineRun) -> StageResult:
        """Run a single pipeline stage."""
        result = StageResult(stage=stage)
        result.started_at = datetime.now(timezone.utc)

        try:
            from brain.llm import BrainLLM
            brain = BrainLLM(provider=self.provider, model=self.model)

            prompt = self._build_stage_prompt(stage, goal, code_context, run)
            start = time.monotonic()
            response = await brain._call(self.provider or "ollama", [
                {"role": "system", "content": self._stage_system_prompt(stage)},
                {"role": "user", "content": prompt},
            ])
            elapsed = int((time.monotonic() - start) * 1000)

            result.output = response
            result.status = "done"
            result.tokens_used = len(response) // 4
            run.total_tokens += result.tokens_used
            run.total_time_ms += elapsed

            # Extract self-check for Simplifier stage
            if stage == PipelineStage.SIMPLIFIER:
                result.self_check = self._extract_self_check(response)

        except Exception as e:
            result.status = "failed"
            result.errors.append(str(e))
            logger.warning(f"[ponytail] stage {stage.value} failed: {e}")

        result.finished_at = datetime.now(timezone.utc)
        return result

    def _stage_system_prompt(self, stage: PipelineStage) -> str:
        """System prompt for each pipeline stage."""
        base = "You are an expert AI coding agent in the DevOS Ponytail pipeline."
        if stage == PipelineStage.SIMPLIFIER:
            return f"{base}\n\n{PONYTAIL_RULES}"
        if stage == PipelineStage.SECURITY:
            return f"{base} You specialize in security auditing. Check for OWASP Top 10 vulnerabilities, injection attacks, insecure dependencies, and missing auth/validation."
        if stage == PipelineStage.TESTER:
            return f"{base} You specialize in testing. Write comprehensive tests covering happy paths, edge cases, error conditions, and regression scenarios."
        if stage == PipelineStage.LEARN:
            return f"{base} Extract actionable lessons from the completed pipeline run. What patterns worked? What should be avoided? What could be automated?"
        return base

    def _build_stage_prompt(self, stage: PipelineStage, goal: str,
                            code_context: str, run: PipelineRun) -> str:
        """Build the prompt for a specific stage."""
        prev_outputs = "\n\n".join(
            f"--- {s.stage.value} output ---\n{s.output[:1000]}"
            for s in run.stages if s.status == "done"
        )

        prompts = {
            PipelineStage.ARCHITECT: f"""GOAL: {goal}

Design the architecture for this task. Consider:
- Components and their responsibilities
- Data flow and interfaces
- Technology choices
- Error handling strategy
- Testing approach

Respond with a clear architectural plan.""",

            PipelineStage.PLANNER: f"""GOAL: {goal}

{prev_outputs}

Break this into ordered subtasks. Each subtask needs:
- A short id (t1, t2, ...)
- A one-sentence description
- Dependencies on other subtasks

Respond with JSON: {{"subtasks": [{{"id": "t1", "description": "...", "depends_on": []}}]}}""",

            PipelineStage.ENGINEER: f"""GOAL: {goal}

{prev_outputs}

{code_context}

Write the implementation code. Be thorough — handle errors, edge cases, and write clean, documented code.""",

            PipelineStage.SIMPLIFIER: f"""GOAL: {goal}

{prev_outputs}

{PONYTAIL_RULES}

Review the code and:
1. Remove any unnecessary code, dead code, or over-engineering
2. Ensure exactly one runnable self-check command exists
3. Verify safety-critical code (validation, auth, error handling) is intact
4. Replace with library calls where appropriate
5. Add the self-check command as a comment: # SELF-CHECK: <command>""",

            PipelineStage.REVIEWER: f"""GOAL: {goal}

{prev_outputs}

Review the code for:
- Correctness and completeness
- Code style and best practices
- Performance issues
- Documentation quality
- Potential bugs""",

            PipelineStage.SECURITY: f"""GOAL: {goal}

{prev_outputs}

Security audit — check for:
- OWASP Top 10 vulnerabilities
- Injection attacks (SQL, command, prompt)
- Insecure dependencies
- Missing authentication/authorization
- Sensitive data exposure
- Insecure defaults""",

            PipelineStage.TESTER: f"""GOAL: {goal}

{prev_outputs}

Write comprehensive tests:
- Unit tests for each function
- Integration tests for workflows
- Edge case and error condition tests
- The self-check command from the Simplifier stage must pass""",

            PipelineStage.CHAOS: f"""GOAL: {goal}

{prev_outputs}

Chaos/fuzz testing:
- What happens with empty/null inputs?
- What happens with extremely large inputs?
- What happens with concurrent access?
- What happens when dependencies fail?
- What are the failure modes?""",

            PipelineStage.FIX: f"""GOAL: {goal}

{prev_outputs}

Fix all issues found in the review, security audit, testing, and chaos testing stages.
Every issue must be addressed or explicitly documented as accepted risk.""",

            PipelineStage.RETEST: f"""GOAL: {goal}

{prev_outputs}

Final verification:
- Run all tests
- Verify the self-check passes
- Confirm no regressions
- Confirm all fixes are applied
Report pass/fail with details.""",

            PipelineStage.DEPLOY: f"""GOAL: {goal}

{prev_outputs}

Prepare for deployment:
- Build/compile if needed
- Generate deployment artifacts
- Update documentation
- Provide deployment instructions""",

            PipelineStage.LEARN: f"""GOAL: {goal}

{prev_outputs}

Extract lessons from this pipeline run:
- What patterns were successful?
- What should be avoided in future?
- What could be automated?
- What reusable components emerged?

Respond with a bullet list of actionable lessons.""",
        }

        return prompts.get(stage, f"GOAL: {goal}\n\n{prev_outputs}")

    def _extract_self_check(self, output: str) -> Optional[str]:
        """Extract the self-check command from the Simplifier output."""
        import re
        match = re.search(r'#\s*SELF-CHECK:\s*(.+)', output)
        if match:
            return match.group(1).strip()
        return None


async def run_ponytail_pipeline(goal: str, code_context: str = "",
                                provider: Optional[str] = None,
                                model: Optional[str] = None) -> PipelineRun:
    """Convenience: run the full Ponytail pipeline."""
    pipeline = PonytailPipeline(provider, model)
    return await pipeline.run(goal, code_context)