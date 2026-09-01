"""
Cognitive System — Intent Parser (Agency OS Master Plan §2).

Converts natural-language goals into structured IntentRequests with
IdentityContext, expected outcomes, and capability requirements. This is the
first stage of the Intent→Identity→Expected Outcome→Capabilities→Workers→
Validation→Execution→Reflection model.

Before this module, the BrainExecutionLoop received goals as raw strings and
worked through them step-by-step with no upfront structure. IntentParser adds
the missing upfront parsing: what is the user actually asking for, what would
"done" look like, and what capabilities/workers are likely needed?
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("devos.cognitive.intent")


INTENT_PARSE_PROMPT = """Analyze this user request and extract its structured intent.

REQUEST: {goal}

Respond ONLY with JSON, no markdown fences:
{{
  "goal_type": "one of: build, fix, analyze, research, deploy, refactor, explain, automate, other",
  "summary": "one-sentence summary of what the user wants",
  "expected_outcome": "concrete description of what success looks like",
  "constraints": ["list of any constraints mentioned"],
  "suggested_capabilities": ["list of UCIP capabilities likely needed"],
  "suggested_workers": ["list of worker persona slugs that might help"],
  "complexity": "simple | medium | complex | multi-agent",
  "estimated_steps": <number 1-20>
}}"""


@dataclass
class Intent:
    """Structured representation of a user's goal, parsed before execution."""
    raw_goal:          str
    goal_type:         str = "other"
    summary:           str = ""
    expected_outcome:  str = ""
    constraints:       list[str] = field(default_factory=list)
    suggested_capabilities: list[str] = field(default_factory=list)
    suggested_workers: list[str] = field(default_factory=list)
    complexity:        str = "simple"
    estimated_steps:   int = 1

    def to_dict(self) -> dict:
        return {
            "raw_goal": self.raw_goal,
            "goal_type": self.goal_type,
            "summary": self.summary,
            "expected_outcome": self.expected_outcome,
            "constraints": self.constraints,
            "suggested_capabilities": self.suggested_capabilities,
            "suggested_workers": self.suggested_workers,
            "complexity": self.complexity,
            "estimated_steps": self.estimated_steps,
        }


class IntentParser:
    """Parses natural-language goals into structured Intent objects using
    the LLM. Falls back to a basic heuristic parser if the LLM is unavailable."""

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider
        self.model = model

    async def parse(self, goal: str) -> Intent:
        """Parse a goal into a structured Intent. Tries LLM first, falls back
        to heuristic parsing."""
        try:
            from brain.llm import BrainLLM
            brain = BrainLLM(provider=self.provider, model=self.model)
            prompt = INTENT_PARSE_PROMPT.format(goal=goal)
            response = await brain._call(self.provider or "ollama", [
                {"role": "user", "content": prompt}
            ])
            data = json.loads(response.strip())
            return Intent(
                raw_goal=goal,
                goal_type=data.get("goal_type", "other"),
                summary=data.get("summary", ""),
                expected_outcome=data.get("expected_outcome", ""),
                constraints=data.get("constraints", []),
                suggested_capabilities=data.get("suggested_capabilities", []),
                suggested_workers=data.get("suggested_workers", []),
                complexity=data.get("complexity", "simple"),
                estimated_steps=data.get("estimated_steps", 1),
            )
        except Exception as e:
            logger.warning(f"[intent] LLM parse failed, using heuristic: {e}")
            return self._heuristic_parse(goal)

    def _heuristic_parse(self, goal: str) -> Intent:
        """Basic heuristic parsing when LLM is unavailable."""
        g = goal.lower()
        if any(w in g for w in ["build", "create", "make", "generate", "write"]):
            goal_type = "build"
        elif any(w in g for w in ["fix", "debug", "repair", "correct"]):
            goal_type = "fix"
        elif any(w in g for w in ["analyze", "review", "audit", "examine"]):
            goal_type = "analyze"
        elif any(w in g for w in ["research", "find", "search", "look up"]):
            goal_type = "research"
        elif any(w in g for w in ["deploy", "publish", "release", "ship"]):
            goal_type = "deploy"
        elif any(w in g for w in ["refactor", "improve", "optimize", "clean"]):
            goal_type = "refactor"
        elif any(w in g for w in ["explain", "describe", "what is", "how does"]):
            goal_type = "explain"
        elif any(w in g for w in ["automate", "schedule", "run every"]):
            goal_type = "automate"
        else:
            goal_type = "other"

        # Estimate complexity from word count
        words = len(goal.split())
        if words < 5:
            complexity, steps = "simple", 1
        elif words < 20:
            complexity, steps = "medium", 3
        else:
            complexity, steps = "complex", 8

        return Intent(
            raw_goal=goal,
            goal_type=goal_type,
            summary=goal[:100],
            expected_outcome="",
            constraints=[],
            suggested_capabilities=[],
            suggested_workers=[],
            complexity=complexity,
            estimated_steps=steps,
        )


async def parse_intent(goal: str, provider: Optional[str] = None,
                       model: Optional[str] = None) -> Intent:
    """Convenience: parse a goal into a structured Intent."""
    return await IntentParser(provider, model).parse(goal)