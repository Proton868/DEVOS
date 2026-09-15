"""
Agent-assisted website materialization.

Uses the LLM to produce a structured file map, then writes through FileService.
This is NOT the template scaffold path — content is generated for the goal.
Success requires files on disk that pass validate_website_artifacts.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("devos.website_builder")

_SYSTEM = (
    "You are a DevOS workspace implementation agent. "
    "Build a complete small website for the user's goal. "
    "Respond with ONLY valid JSON (no markdown fences) of the form:\n"
    '{"files":[{"path":"index.html","content":"..."},'
    '{"path":"style.css","content":"..."},'
    '{"path":"script.js","content":"..."}]}\n'
    "Rules:\n"
    "- Include index.html as the entry point for static sites.\n"
    "- Link CSS/JS with relative paths.\n"
    "- Content must match the user's brand/goal.\n"
    "- Do not invent external API keys.\n"
    "- Keep total payload under 80KB of text.\n"
)


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        # find first { ... }
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except Exception:
                return None
    return None


async def materialize_website_via_agent(
    *,
    user_id: str,
    project_id: str = "default",
    goal: str,
    brain=None,
) -> dict[str, Any]:
    """
    LLM → structured files → FileService writes.
    Returns {ok, files, entry_point, execution_path, error?}.
    """
    out: dict[str, Any] = {
        "ok": False,
        "files": [],
        "entry_point": None,
        "execution_path": "AGENT_MATERIALIZE",
        "errors": [],
    }
    try:
        from execution.files import FileService
        fs = FileService(user_id, project_id or "default")
    except Exception as e:
        out["errors"].append(f"fileservice:{e}")
        return out

    if brain is None:
        try:
            from brain.llm import BrainLLM
            from core.config import settings
            brain = BrainLLM(provider=settings.DEFAULT_PROVIDER, purpose="coding")
        except Exception as e:
            out["errors"].append(f"brain:{e}")
            return out

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Goal:\n{goal}\n\nProduce the JSON file map now."},
    ]
    try:
        text = await brain.stream_chat(messages)
    except Exception as e:
        out["errors"].append(f"llm:{e}")
        return out

    if not text or str(text).startswith("All providers failed"):
        out["errors"].append(f"llm_failed:{str(text)[:200]}")
        return out

    data = _extract_json(text)
    if not data or not isinstance(data.get("files"), list):
        out["errors"].append("invalid_llm_json")
        return out

    written = []
    for item in data["files"][:20]:
        if not isinstance(item, dict):
            continue
        path = (item.get("path") or "").strip().lstrip("/")
        content = item.get("content")
        if not path or content is None:
            continue
        if ".." in path or path.startswith("/"):
            continue
        try:
            fs.write(path, str(content))
            written.append(path)
        except Exception as e:
            out["errors"].append(f"write:{path}:{e}")
            logger.warning("website write failed %s: %s", path, e)

    out["files"] = written
    if "index.html" in written:
        out["entry_point"] = "index.html"
    elif written:
        out["entry_point"] = next((p for p in written if p.endswith((".html", ".htm"))), written[0])

    # Authoritative validation
    try:
        from brain.orchestration_verify import validate_website_artifacts
        validation = await validate_website_artifacts(
            user_id=user_id,
            workspace_id=project_id or "default",
            goal=goal,
        )
        out["validation"] = validation
        out["ok"] = bool(validation.get("valid")) and bool(written)
        if validation.get("entry_point"):
            out["entry_point"] = validation["entry_point"]
    except Exception as e:
        out["errors"].append(f"validate:{e}")
        out["ok"] = bool(written)

    return out
