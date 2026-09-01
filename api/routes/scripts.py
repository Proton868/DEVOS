"""Scripts route — Brain-managed scripts with full run history"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
from sqlalchemy import select, desc
from core.database import get_db, Script, ScriptRun, ScriptChain
from api.routes.auth import get_current_user
from core.sanitize import sanitize_name, sanitize_freeform, sanitize_name_list

router = APIRouter()

class ScriptCreate(BaseModel):
    name: str; code: str; language: str = "python"
    description: Optional[str] = None
    schedule_type: str = "manual"; schedule_value: Optional[str] = None
    is_active: bool = False
    notify_on_success: str = "none"; notify_on_failure: str = "none"
    retry_policy: str = "none"  # none | once | twice (G9)
    tags: list[str] = []

    def model_post_init(self, __context):
        if self.tags is None:
            self.tags = []

    # security-audit P3f: `name`/`description`/`tags` are persisted and
    # rendered in the Flow UI -- sanitize control characters. `code` is
    # deliberately left untouched since it must remain byte-for-byte
    # verbatim to execute correctly.
    @field_validator("name", mode="before")
    @classmethod
    def _sanitize_name(cls, value):
        return sanitize_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _sanitize_description(cls, value):
        return sanitize_freeform(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _sanitize_tags(cls, value):
        return sanitize_name_list(value)

class ScriptUpdate(BaseModel):
    name: Optional[str]=None; code: Optional[str]=None; language: Optional[str]=None
    schedule_type: Optional[str]=None; schedule_value: Optional[str]=None
    is_active: Optional[bool]=None; tags: Optional[list[str]]=None
    notify_on_success: Optional[str]=None; notify_on_failure: Optional[str]=None
    retry_policy: Optional[str]=None

    @field_validator("name", mode="before")
    @classmethod
    def _sanitize_name(cls, value):
        return sanitize_name(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _sanitize_tags(cls, value):
        return sanitize_name_list(value) if value is not None else value

@router.get("")
async def list_scripts(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Script).where(Script.owner_id==user.id).order_by(desc(Script.created_at)))
    return [{"id":s.id,"name":s.name,"language":s.language,"schedule_type":s.schedule_type,"is_active":s.is_active,"tags":s.tags,"retry_policy":s.retry_policy,"notify_on_success":s.notify_on_success,"notify_on_failure":s.notify_on_failure} for s in r.scalars().all()]

@router.post("")
async def create_script(req: ScriptCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    s = Script(owner_id=user.id, **req.model_dump())
    db.add(s); await db.commit()
    if s.schedule_type in ("cron", "interval") and s.is_active:
        from api.scheduler import schedule_script
        schedule_script(s)
    return {"id":s.id,"name":s.name,"language":s.language,"description":s.description,
            "schedule_type":s.schedule_type,"schedule_value":s.schedule_value,"webhook_token":s.webhook_token,
            "is_active":s.is_active,"tags":s.tags,"retry_policy":s.retry_policy,
            "notify_on_success":s.notify_on_success,"notify_on_failure":s.notify_on_failure}

@router.get("/{sid}")
async def get_script(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Script).where(Script.id==sid, Script.owner_id==user.id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404)
    return {"id":s.id,"name":s.name,"code":s.code,"language":s.language,"description":s.description,
            "schedule_type":s.schedule_type,"schedule_value":s.schedule_value,"webhook_token":s.webhook_token,
            "is_active":s.is_active,"tags":s.tags,"retry_policy":s.retry_policy,
            "notify_on_success":s.notify_on_success,"notify_on_failure":s.notify_on_failure}

@router.patch("/{sid}")
async def update_script(sid: str, req: ScriptUpdate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Script).where(Script.id==sid, Script.owner_id==user.id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404)
    for k,v in req.model_dump(exclude_none=True).items(): setattr(s, k, v)
    s.updated_at = datetime.now(timezone.utc); await db.commit()
    from api.scheduler import schedule_script, unschedule_script
    if s.schedule_type in ("cron", "interval") and s.is_active:
        schedule_script(s)   # live-reschedule with whatever changed (schedule_value, code, etc.)
    else:
        unschedule_script(s.id)   # switched to manual, or deactivated -- stop any live job
    return {"status":"updated"}

@router.delete("/{sid}")
async def delete_script(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Script).where(Script.id==sid, Script.owner_id==user.id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404)
    from api.scheduler import unschedule_script
    unschedule_script(s.id)
    await db.delete(s); await db.commit()
    return {"status":"deleted"}

@router.post("/{sid}/run")
async def run_script(sid: str, request: Request, background_tasks: BackgroundTasks, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Script).where(Script.id==sid, Script.owner_id==user.id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404)
    from execution.script_runner import run_and_record
    background_tasks.add_task(run_and_record, s.id, "manual")
    return {"status":"queued"}


@router.post("/webhook/{token}")
async def webhook_trigger(token: str, request: Request, background_tasks: BackgroundTasks, db=Depends(get_db)):
    """Fire a script by its webhook_token -- no auth required, since the
    token itself is the credential (same model as GitHub/Stripe webhooks).
    Lets external services (cron-job.org, GitHub Actions, Zapier, curl, etc.)
    trigger a Flow script over plain HTTP, closing the real gap where
    Script.webhook_token existed on the model but nothing ever read it."""
    r = await db.execute(select(Script).where(Script.webhook_token == token))
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Unknown webhook token")
    if not s.is_active and s.schedule_type not in ("manual",):
        # Inactive scheduled scripts stay triggerable via webhook -- only
        # `is_active` gates the *scheduler*, not manual/webhook invocation.
        pass
    from execution.script_runner import run_and_record
    background_tasks.add_task(run_and_record, s.id, "webhook")
    return {"status": "queued", "script": s.name}


@router.post("/{sid}/webhook/rotate")
async def rotate_webhook_token(sid: str, request: Request, db=Depends(get_db)):
    """Issue a new webhook token for a script, invalidating the old one --
    the standard "rotate a leaked credential" action for webhook-style auth."""
    user = await get_current_user(request, db)
    r = await db.execute(select(Script).where(Script.id==sid, Script.owner_id==user.id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404)
    from core.database import gen_id
    s.webhook_token = gen_id()
    s.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"webhook_token": s.webhook_token}

@router.get("/{sid}/runs")
async def get_runs(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(ScriptRun).where(ScriptRun.script_id==sid).order_by(desc(ScriptRun.started_at)).limit(20))
    return [{"id":r.id,"status":r.status,"exit_code":r.exit_code,"duration_ms":r.duration_ms,"trigger":r.trigger,"started_at":r.started_at,"stdout":r.stdout,"stderr":r.stderr} for r in r.scalars().all()]

@router.post("/{sid}/ai-debug")
async def ai_debug(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    sr = await db.execute(select(Script).where(Script.id==sid, Script.owner_id==user.id))
    s = sr.scalar_one_or_none()
    if not s: raise HTTPException(404)
    rr = await db.execute(select(ScriptRun).where(ScriptRun.script_id==sid, ScriptRun.status=="failed").order_by(desc(ScriptRun.started_at)).limit(1))
    run = rr.scalar_one_or_none()
    if not run: raise HTTPException(400, "No failed runs")
    from brain.llm import BrainLLM
    brain = BrainLLM()
    fixed = await brain.stream_chat([
        {"role":"system","content":f"You are an expert {s.language} debugger. Return ONLY the fixed code, no explanation."},
        {"role":"user","content":f"CODE:\n```{s.language}\n{s.code}\n```\n\nERROR:\n{run.stderr or run.stdout}\n\nFixed code:"}
    ])
    # Check for the fallback string returned when ALL providers failed
    # (brain/llm.py stream_chat returns "All providers failed..." as a
    # last-resort string, not an actual fixed-code response). Treating
    # that as valid code would silently return garbage to the user.
    if fixed.startswith("All providers failed"):
        raise HTTPException(
            503,
            "All LLM providers are currently unreachable. "
            "Check your API keys, Ollama connection, and network access."
        )
    # Extract code from the first fenced block, if any; else use the raw text
    import re as _re
    fence_match = _re.search(r"```(?:\w+)?\n?(.*?)```", fixed, _re.DOTALL)
    if fence_match:
        fixed = fence_match.group(1)
    return {"fixed_code": fixed.strip(), "original_error": run.stderr}


# ── Script chaining (G8) ─────────────────────────────────────────
# Basic conditional branching for Flow: run a child script automatically
# when a parent finishes, gated on success/failure. Actual chain
# execution lives in execution/script_runner.py's run_and_record(), which
# every trigger path (manual, scheduled, webhook) now goes through.

class ChainCreate(BaseModel):
    parent_script_id: str
    child_script_id: str
    condition: str = "on_success"  # on_success | on_failure


async def _own_script_or_404(db, user_id: str, script_id: str) -> Script:
    r = await db.execute(select(Script).where(Script.id == script_id, Script.owner_id == user_id))
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, f"Script not found: {script_id}")
    return s


@router.get("/chains/all")
async def list_chains(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(ScriptChain, Script)
        .join(Script, Script.id == ScriptChain.parent_script_id)
        .where(Script.owner_id == user.id)
        .order_by(desc(ScriptChain.created_at))
    )
    rows = r.all()
    return [
        {"id": c.id, "parent_script_id": c.parent_script_id, "child_script_id": c.child_script_id,
         "condition": c.condition, "enabled": c.enabled, "created_at": c.created_at}
        for c, _parent in rows
    ]


@router.post("/chains")
async def create_chain(req: ChainCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await _own_script_or_404(db, user.id, req.parent_script_id)
    await _own_script_or_404(db, user.id, req.child_script_id)
    if req.condition not in ("on_success", "on_failure"):
        raise HTTPException(400, "condition must be on_success or on_failure")
    if req.parent_script_id == req.child_script_id:
        raise HTTPException(400, "A script cannot chain to itself")
    chain = ScriptChain(parent_script_id=req.parent_script_id,
                         child_script_id=req.child_script_id,
                         condition=req.condition)
    db.add(chain)
    await db.commit()
    return {"id": chain.id}


@router.patch("/chains/{chain_id}")
async def update_chain(chain_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(ScriptChain, Script)
        .join(Script, Script.id == ScriptChain.parent_script_id)
        .where(ScriptChain.id == chain_id, Script.owner_id == user.id)
    )
    row = r.first()
    if not row:
        raise HTTPException(404)
    chain, _parent = row
    chain.enabled = not chain.enabled
    await db.commit()
    return {"id": chain.id, "enabled": chain.enabled}


@router.delete("/chains/{chain_id}")
async def delete_chain(chain_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(ScriptChain, Script)
        .join(Script, Script.id == ScriptChain.parent_script_id)
        .where(ScriptChain.id == chain_id, Script.owner_id == user.id)
    )
    row = r.first()
    if not row:
        raise HTTPException(404)
    chain, _parent = row
    await db.delete(chain)
    await db.commit()
    return {"status": "deleted"}
