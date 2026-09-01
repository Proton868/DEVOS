from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.database import get_db
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from api.deps import tenant_ctx

from core.config import settings
import asyncio

router = APIRouter()

@router.get("")
async def list_all(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.llm import BrainLLM
    results = await asyncio.gather(*[BrainLLM(provider=p).list_models(p) for p in settings.available_providers], return_exceptions=True)
    return {"models": [m for r in results if isinstance(r,list) for m in r], "providers": settings.available_providers}

@router.get("/settings")
async def get_settings(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from memory.store import MemoryStore
    return {"providers": settings.available_providers, "default_provider": settings.DEFAULT_PROVIDER,
            "ollama_host": settings.OLLAMA_HOST, "has_tavily": settings.has_tavily,
            "has_supabase": settings.has_supabase, "memory_backend": MemoryStore().backend}


# ── Editable provider configuration ─────────────────────────────────────
class ProviderConfigUpdate(BaseModel):
    """All fields optional — only supplied keys are changed. Matches the
    whitelist in core/config.py's EDITABLE_PROVIDER_KEYS."""
    DEFAULT_PROVIDER: str | None = None
    OLLAMA_HOST: str | None = None
    OLLAMA_DEFAULT_MODEL: str | None = None
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_BASE_URL: str | None = None
    OPENROUTER_DEFAULT_MODEL: str | None = None
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_BASE_URL: str | None = None
    DEEPSEEK_DEFAULT_MODEL: str | None = None
    GEMINI_API_KEY: str | None = None
    GEMINI_DEFAULT_MODEL: str | None = None
    OPENAI_API_KEY: str | None = None
    HUGGINGFACE_API_KEY: str | None = None
    HUGGINGFACE_BASE_URL: str | None = None
    HUGGINGFACE_DEFAULT_MODEL: str | None = None
    NARAROUTER_API_KEY: str | None = None
    NARAROUTER_BASE_URL: str | None = None
    NARAROUTER_DEFAULT_MODEL: str | None = None
    SUPABASE_URL: str | None = None
    SUPABASE_KEY: str | None = None
    TAVILY_API_KEY: str | None = None


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return value[:3] + "…" + value[-4:]

def _is_secret_key(key: str) -> bool:
    k = key.upper()
    return any(s in k for s in ("KEY", "SECRET", "TOKEN", "PASSWORD"))

@router.get("/providers/config")
async def get_provider_config(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from core.config import EDITABLE_PROVIDER_KEYS
    out = {}
    for k in EDITABLE_PROVIDER_KEYS:
        val = getattr(settings, k, "") or ""
        if _is_secret_key(k):
            out[k] = {"configured": bool(val), "masked": _mask_secret(val) if val else ""}
        else:
            out[k] = val
    out["_meta"] = {"is_admin": bool(getattr(user, "is_admin", False))}
    return out



@router.put("/providers/config")
async def save_provider_config(req: ProviderConfigUpdate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Admin required to change provider configuration")
    """Persist provider/model settings edited from the Settings UI to .env
    and apply them live — no server restart required."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from core.config import update_env_settings
    updates = req.model_dump(exclude_none=True)
    if not updates:
        return {"updated": {}}
    try:
        updated = update_env_settings(updates)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"updated": updated}


class TestConnectionReq(BaseModel):
    provider: str


@router.post("/providers/test")
async def test_provider_connection(req: TestConnectionReq, request: Request, db=Depends(get_db)):
    """Send a trivial 1-token completion to verify a provider's credentials
    and connectivity actually work, rather than just checking a key is set."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.llm import BrainLLM
    try:
        brain = BrainLLM(provider=req.provider)
        reply = await brain.stream_chat(
            [{"role": "user", "content": "Reply with exactly: OK"}],
        )
        if isinstance(reply, str) and reply.startswith("All providers failed"):
            return {"ok": False, "provider": req.provider, "error": reply}
        return {"ok": True, "provider": req.provider, "sample": (reply or "")[:120]}
    except Exception as e:
        return {"ok": False, "provider": req.provider, "error": str(e)}


# ── AI inline code completion (Cursor-style ghost text) ─────────────────
class CompleteReq(BaseModel):
    providerId: Optional[str] = None
    model: Optional[str] = None
    prefix: str = ""
    suffix: str = ""
    language: Optional[str] = None
    filepath: Optional[str] = None


@router.post("/complete")
async def complete(req: CompleteReq, request: Request, db=Depends(get_db)):
    """Fill-in-the-middle style single completion for Monaco's inline
    completions provider (see frontend CodeEditor.jsx's setupAutocomplete).
    Uses the same BrainLLM used everywhere else in the codebase, with a
    tight system prompt asking for ONLY the code to insert -- no markdown
    fences, no explanation -- since this gets inserted directly as ghost
    text with no post-processing beyond a fence-strip safety net below."""
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    from brain.llm import BrainLLM
    brain = BrainLLM(provider=req.providerId, model=req.model, user_id=user.id)
    system = (
        "You are a code completion engine, like GitHub Copilot. Given code "
        "before and after the cursor, output ONLY the text that should be "
        "inserted at the cursor to continue the code naturally. "
        "Rules: no markdown fences, no explanation, no repeating the "
        "prefix or suffix, just the missing code. Keep it short -- usually "
        "a single line or a few lines. If nothing sensible completes the "
        "code, output nothing."
    )
    lang_hint = f" Language: {req.language}." if req.language else ""
    user_msg = (
        f"{lang_hint}\n--- CODE BEFORE CURSOR ---\n{req.prefix}\n"
        f"--- CODE AFTER CURSOR ---\n{req.suffix}\n--- INSERT HERE ---"
    )
    try:
        text = await brain.stream_chat([
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ])
        if text.startswith("All providers failed"):
            return {"completion": ""}
        # Safety net: strip a fenced block if the model added one anyway.
        import re as _re
        m = _re.match(r"^```(?:\w+)?\n?(.*?)\n?```$", text.strip(), _re.DOTALL)
        completion = m.group(1) if m else text
        # Guard against the model echoing the whole prefix back.
        if completion.strip() and completion.strip() not in req.prefix[-len(completion.strip()) - 5:]:
            return {"completion": completion}
        return {"completion": ""}
    except Exception:
        return {"completion": ""}
