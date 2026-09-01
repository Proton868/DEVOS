"""Composer route — Cursor-style multi-file AI edits.

Three-phase flow, mirroring frontend/composer/ComposerPanel.jsx exactly:
  1. POST /plan     -> BrainLLM analyzes the instruction + project file tree
                       and returns a structured plan of which files to touch.
  2. POST /execute  -> SSE stream; for each planned file, BrainLLM generates
                       the new content, a line-diff is computed against the
                       current file (if any), and a `file_done`/`file_skip`
                       event is streamed back. Nothing is written to disk yet.
  3. POST /apply    -> Writes the user-approved {path, content} pairs to disk
                       via the same FileService used by the IDE file tree, so
                       Composer-authored changes show up exactly like any
                       other edit (git panel, file tree refresh, etc.).

No new dependency, no new storage — this is pure orchestration over
BrainLLM + FileService, the same primitives every other AI route in this
codebase already uses.
"""
import asyncio
import json
import difflib
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.database import get_db
from api.routes.auth import get_current_user
from execution.files import FileService, PathViolation

router = APIRouter()


class ActiveFileRef(BaseModel):
    path: str


class PlanReq(BaseModel):
    providerId: Optional[str] = None
    model: Optional[str] = None
    instruction: str
    activeFile: Optional[ActiveFileRef] = None
    projectId: Optional[str] = "default"


def _project_tree_summary(fs: FileService, limit: int = 200) -> str:
    """Plain-text file listing handed to the model as codebase context —
    same tree data the IDE's file explorer already renders, just flattened
    to text so the model can reason about which files exist."""
    items = fs.tree()
    lines = [it["path"] for it in items if it["type"] == "file"][:limit]
    return "\n".join(lines) if lines else "(empty project)"


def _extract_json(text: str) -> Optional[dict]:
    """Models occasionally wrap JSON in markdown fences or add stray prose;
    pull out the first {...} block and parse it defensively."""
    text = text.strip()
    import re as _re
    m = _re.match(r"^```(?:json)?\n?(.*?)\n?```$", text, _re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


@router.post("/plan")
async def plan(req: PlanReq, request: Request, db=Depends(get_db)):
    """Ask BrainLLM to produce a structured multi-file change plan. Returns
    {summary, notes, files: [{path, change, risk}]} — the exact shape
    ComposerPanel.jsx expects for its review UI."""
    user = await get_current_user(request, db)
    from brain.llm import BrainLLM
    brain = BrainLLM(provider=req.providerId, model=req.model, user_id=user.id)

    fs = FileService(user.id, req.projectId or "default")
    tree_text = _project_tree_summary(fs)
    active_hint = f"\nActive file (context): {req.activeFile.path}" if req.activeFile else ""

    system = (
        "You are Composer, a multi-file AI coding assistant like Cursor's. "
        "Given a codebase file listing and a user instruction, decide which "
        "existing files need changes and/or which new files need creating "
        "to satisfy the instruction. Output ONLY a single JSON object, no "
        "markdown fences, no explanation, in this exact shape:\n"
        '{"summary": "one sentence describing the overall change", '
        '"notes": "optional caveats or empty string", '
        '"files": [{"path": "relative/path.ext", "change": "one sentence '
        'describing what changes in this file", "risk": "low|medium|high"}]}'
        "\nLimit to at most 8 files. Prefer editing existing files over "
        "creating new ones unless the instruction clearly requires new files."
    )
    user_msg = (
        f"PROJECT FILES:\n{tree_text}{active_hint}\n\n"
        f"INSTRUCTION: {req.instruction}"
    )

    try:
        text = await brain.stream_chat([
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ])
        if text.startswith("All providers failed"):
            from fastapi import HTTPException
            raise HTTPException(502, text)
        parsed = _extract_json(text)
        if not parsed or "files" not in parsed:
            from fastapi import HTTPException
            raise HTTPException(502, "AI did not return a valid plan. Try rephrasing the instruction.")
        parsed.setdefault("summary", req.instruction[:120])
        parsed.setdefault("notes", "")
        for f in parsed.get("files", []):
            f.setdefault("change", "")
            f.setdefault("risk", "low")
            if f["risk"] not in ("low", "medium", "high"):
                f["risk"] = "low"
        return parsed
    except Exception as e:
        from fastapi import HTTPException
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(500, str(e))


class PlanFile(BaseModel):
    path: str
    change: str = ""
    risk: str = "low"


class ExecutePlan(BaseModel):
    summary: str = ""
    notes: str = ""
    files: list[PlanFile] = []


class ExecuteReq(BaseModel):
    providerId: Optional[str] = None
    model: Optional[str] = None
    instruction: str
    plan: ExecutePlan
    activeFile: Optional[ActiveFileRef] = None
    projectId: Optional[str] = "default"


def _diff_lines(old: str, new: str) -> list[dict]:
    """Line-level diff, shaped for ComposerPanel.jsx's DiffView component
    ({line, type: 'add'|'del'|'ctx', text})."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    out = []
    ln = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                ln += 1
                out.append({"line": ln, "type": "ctx", "text": old_lines[k]})
        elif tag == "delete":
            for k in range(i1, i2):
                ln += 1
                out.append({"line": ln, "type": "del", "text": old_lines[k]})
        elif tag == "insert":
            for k in range(j1, j2):
                out.append({"line": ln, "type": "add", "text": new_lines[k]})
        elif tag == "replace":
            for k in range(i1, i2):
                ln += 1
                out.append({"line": ln, "type": "del", "text": old_lines[k]})
            for k in range(j1, j2):
                out.append({"line": ln, "type": "add", "text": new_lines[k]})
    return out[:400]


@router.post("/execute")
async def execute(req: ExecuteReq, request: Request, db=Depends(get_db)):
    """Streams one event per planned file: `file_done` (with diff + updated
    content) or `file_skip` (if generation failed for that file), followed by
    a final `compose_done`. Nothing is written to disk here -- see /apply."""
    user = await get_current_user(request, db)
    from brain.llm import BrainLLM
    brain = BrainLLM(provider=req.providerId, model=req.model, user_id=user.id)
    fs = FileService(user.id, req.projectId or "default")

    async def sse():
        try:
            for pf in req.plan.files:
                try:
                    try:
                        existing = fs.read(pf.path)
                        current_content = existing.get("content") or ""
                        file_exists = True
                    except FileNotFoundError:
                        current_content = ""
                        file_exists = False

                    system = (
                        "You are Composer, a multi-file AI coding assistant. "
                        "Output ONLY the complete new contents of the file "
                        "below -- no markdown fences, no explanation, no "
                        "commentary. Preserve unrelated existing code exactly."
                    )
                    user_msg = (
                        f"OVERALL INSTRUCTION: {req.instruction}\n"
                        f"FILE: {pf.path}\n"
                        f"PLANNED CHANGE: {pf.change}\n\n"
                        f"{'CURRENT FILE CONTENTS:' if file_exists else '(this is a new file)'}\n"
                        f"{current_content}"
                    )
                    text = await brain.stream_chat([
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ])
                    if text.startswith("All providers failed"):
                        yield f"data: {json.dumps({'type': 'file_skip', 'path': pf.path, 'reason': text})}\n\n"
                        await asyncio.sleep(0)
                        continue
                    import re as _re
                    m = _re.match(r"^```(?:\w+)?\n?(.*?)\n?```$", text.strip(), _re.DOTALL)
                    updated = m.group(1) if m else text

                    diff = _diff_lines(current_content, updated)
                    additions = sum(1 for d in diff if d["type"] == "add")
                    deletions = sum(1 for d in diff if d["type"] == "del")

                    yield f"data: {json.dumps({'type': 'file_done', 'path': pf.path, 'change': pf.change, 'additions': additions, 'deletions': deletions, 'diff': diff, 'updated': updated})}\n\n"
                except PathViolation as e:
                    yield f"data: {json.dumps({'type': 'file_skip', 'path': pf.path, 'reason': str(e)})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'file_skip', 'path': pf.path, 'reason': str(e)})}\n\n"
                await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'type': 'compose_done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ApplyChange(BaseModel):
    path: str
    content: str


class ApplyReq(BaseModel):
    changes: list[ApplyChange]
    projectId: Optional[str] = "default"


@router.post("/apply")
async def apply(req: ApplyReq, request: Request, db=Depends(get_db)):
    """Writes each approved {path, content} pair to disk via FileService --
    the same write path the IDE's own file editor uses, so applied Composer
    changes are indistinguishable from manual edits afterward."""
    user = await get_current_user(request, db)
    fs = FileService(user.id, req.projectId or "default")
    written = []
    errors = []
    for ch in req.changes:
        try:
            fs.write(ch.path, ch.content)
            written.append(ch.path)
        except PathViolation as e:
            errors.append({"path": ch.path, "error": str(e)})
        except Exception as e:
            errors.append({"path": ch.path, "error": str(e)})
    return {"written": written, "errors": errors}
