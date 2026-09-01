"""Node-level AI actions — per-node analysis endpoint.

Each action type builds a prompt from real node data (WorkflowStep config,
execution results, error messages) and queries BrainLLM with that context."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from api.routes.auth import get_current_user
from core.database import get_db

logger = logging.getLogger("devos.nodes")
router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class AIActionRequest(BaseModel):
    action: str = Field(..., description="One of: explain, why_failed, optimize, generate_tests, improve_speed")
    context: dict = Field(default_factory=dict, description="Node configuration and state")


# ── Prompt builders per action type ──────────────────────────────────────────

def _prompt_for_explain(node_config: dict) -> str:
    return (
        "You are an expert workflow engineer. Explain what this node does "
        "in plain English, including its purpose, inputs, outputs, and any "
        "conditions it checks. Be concise but thorough.\n\n"
        f"NODE: {node_config.get('name', 'unnamed')}\n"
        f"TYPE: {node_config.get('type', 'capability')}\n"
        f"CONFIG: {node_config}"
    )


def _prompt_for_why_failed(node_config: dict, error: str) -> str:
    return (
        "You are an expert debugger. The following workflow node failed.\n"
        "Diagnose the most likely cause of the failure, and suggest a "
        "specific fix or next step to investigate.\n\n"
        f"NODE: {node_config.get('name', 'unnamed')}\n"
        f"TYPE: {node_config.get('type', 'capability')}\n"
        f"ERROR: {error}\n"
        f"CONFIG: {node_config}"
    )


def _prompt_for_optimize(node_config: dict) -> str:
    return (
        "You are a performance engineer. Analyze this workflow node and "
        "suggest specific optimizations: reduce latency, reduce resource "
        "usage, simplify logic, or improve error handling.\n\n"
        f"NODE: {node_config.get('name', 'unnamed')}\n"
        f"TYPE: {node_config.get('type', 'capability')}\n"
        f"CONFIG: {node_config}"
    )


def _prompt_for_generate_tests(node_config: dict) -> str:
    return (
        "You are a test engineer. Generate Python test code for this "
        "workflow node's capability. Write runnable pytest tests that "
        "cover the happy path, edge cases, and error handling. Use the "
        "same pytest conventions as the rest of the codebase.\n\n"
        f"NODE: {node_config.get('name', 'unnamed')}\n"
        f"TYPE: {node_config.get('type', 'capability')}\n"
        f"CAPABILITY: {node_config.get('capability', 'unknown')}\n"
        f"INPUTS: {node_config.get('inputs', {})}\n"
        f"OUTPUTS: {node_config.get('outputs', {})}"
    )


def _prompt_for_improve_speed(node_config: dict) -> str:
    return (
        "You are a performance expert. Suggest specific speed improvements "
        "for this workflow node: caching, parallelization, batching, "
        "algorithmic improvements, or reducing unnecessary work.\n\n"
        f"NODE: {node_config.get('name', 'unnamed')}\n"
        f"TYPE: {node_config.get('type', 'capability')}\n"
        f"CONFIG: {node_config}"
    )


PROMPT_BUILDERS = {
    "explain": _prompt_for_explain,
    "why_failed": _prompt_for_why_failed,
    "optimize": _prompt_for_optimize,
    "generate_tests": _prompt_for_generate_tests,
    "improve_speed": _prompt_for_improve_speed,
}


@router.post("/{node_id}/ai-action")
async def ai_action(node_id: str, req: AIActionRequest, request: Request, db=Depends(get_db)):
    """Run an AI analysis action on a specific workflow node."""
    user = await get_current_user(request, db)

    if req.action not in PROMPT_BUILDERS:
        raise HTTPException(400, f"Unknown action '{req.action}'. Valid: {', '.join(PROMPT_BUILDERS)}")

    node_config = req.context.get("config", {})
    node_config["name"] = node_config.get("name", node_id)

    # For "why_failed", pull the error from context or check ScriptRun history
    error = req.context.get("error", "")
    if req.action == "why_failed" and not error:
        # Try to find the most recent failed ScriptRun for this node
        try:
            from sqlalchemy import select, desc
            from core.database import ScriptRun, Script, AsyncSessionLocal
            async with AsyncSessionLocal() as sdb:
                r = await sdb.execute(
                    select(ScriptRun)
                    .join(Script, ScriptRun.script_id == Script.id)
                    .where(ScriptRun.status == "failed")
                    .order_by(desc(ScriptRun.started_at))
                    .limit(1)
                )
                run = r.scalar_one_or_none()
                if run:
                    error = run.stderr or run.stdout or "Unknown error"
        except Exception:
            pass

    if req.action == "why_failed" and not error:
        return {
            "node_id": node_id,
            "action": req.action,
            "result": "No failed run found for this node. Cannot diagnose a failure that hasn't happened.",
        }

    # Build the prompt and call the LLM
    from brain.llm import BrainLLM
    brain = BrainLLM(user_id=user.id)

    builder = PROMPT_BUILDERS[req.action]
    if req.action == "why_failed":
        prompt = builder(node_config, error)
    else:
        prompt = builder(node_config)

    try:
        text = await brain.stream_chat([
            {"role": "system", "content": "You are DevOS, an AI assistant for workflow engineering."},
            {"role": "user", "content": prompt},
        ])
        if text.startswith("All providers failed"):
            raise HTTPException(502, "LLM provider unavailable")

        return {
            "node_id": node_id,
            "action": req.action,
            "result": text,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[nodes] ai-action failed for {node_id}/{req.action}: {e}")
        raise HTTPException(500, f"AI action failed: {e}")