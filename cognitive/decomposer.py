"""
Cognitive System — Goal Decomposition.

Closes a real gap: core/loop.py's BrainExecutionLoop is a flat ReAct loop
(think -> act -> observe, repeat) with no upfront planning step at all —
every goal, simple or complex, gets worked one action at a time with no
visibility into how many distinct pieces of work it actually contains.
This module adds a genuine planning step *before* execution starts.

Scope note: this produces a plan for a single Brain to work through
sequentially, not a dispatch table for multiple parallel Workers — that's
correctly Stage 3 territory (Workers don't exist yet). This is still real
value on its own: the Brain gets an explicit checklist instead of
figuring out task structure implicitly step-by-step, and the plan is
visible in LoopState for observability/audit.
"""
import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("devos.cognitive.decomposer")

DECOMPOSE_PROMPT_TEMPLATE = """Break this goal into an ordered list of concrete subtasks.

GOAL: {goal}

Rules:
- 1-8 subtasks. If the goal is already a single atomic action, return exactly one subtask.
- Each subtask needs a short id (e.g. "t1", "t2") and a one-sentence description.
- depends_on lists the ids of subtasks that must finish first (empty list if none).
- Do not invent subtasks the goal didn't ask for — decompose, don't expand scope.

Respond ONLY with JSON, no markdown fences:
{{"subtasks": [{{"id": "t1", "description": "...", "depends_on": []}}, ...]}}"""


@dataclass
class Subtask:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | done | failed

    def to_dict(self) -> dict:
        return {"id": self.id, "description": self.description,
                "depends_on": self.depends_on, "status": self.status}


class DecompositionError(Exception):
    pass


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
    raise DecompositionError(f"Could not extract JSON from decomposition response: {raw[:200]}")


def _validate_dag(subtasks: list[Subtask]) -> None:
    """Raises DecompositionError if depends_on references a non-existent id
    or forms a cycle. A bad plan should fail loudly here, not silently
    deadlock every subtask's dependencies at execution time."""
    ids = {t.id for t in subtasks}
    for t in subtasks:
        for dep in t.depends_on:
            if dep not in ids:
                raise DecompositionError(f"Subtask '{t.id}' depends_on unknown id '{dep}'")

    # Cycle detection via simple DFS
    visiting, visited = set(), set()

    def visit(tid: str, chain: list[str]):
        if tid in visited:
            return
        if tid in visiting:
            raise DecompositionError(f"Dependency cycle detected: {' -> '.join(chain + [tid])}")
        visiting.add(tid)
        task = next(t for t in subtasks if t.id == tid)
        for dep in task.depends_on:
            visit(dep, chain + [tid])
        visiting.discard(tid)
        visited.add(tid)

    for t in subtasks:
        visit(t.id, [])


class GoalDecomposer:
    async def decompose(self, goal: str, brain) -> list[Subtask]:
        """brain: a BrainLLM instance (dependency-injected so this is
        testable without a live LLM — see tests using a fake brain with a
        canned .decide()-shaped response)."""
        prompt = DECOMPOSE_PROMPT_TEMPLATE.format(goal=goal)
        try:
            raw = await brain._call(brain.provider, [{"role": "user", "content": prompt}])
            parsed = _extract_json(raw)
            subtasks = [Subtask(id=t["id"], description=t["description"],
                                 depends_on=t.get("depends_on", []))
                        for t in parsed["subtasks"]]
            if not subtasks:
                raise DecompositionError("Decomposition returned zero subtasks")
            _validate_dag(subtasks)
            return subtasks
        except Exception as e:
            # Defensive fallback: treat the whole goal as one atomic subtask
            # rather than failing the entire run over a planning-step
            # hiccup. This is logged, not silent, so a pattern of fallbacks
            # is visible rather than masked.
            logger.warning(f"[decomposer] falling back to single-subtask plan: {e}")
            return [Subtask(id="t1", description=goal, depends_on=[])]
