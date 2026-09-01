"""Chat route — plain streaming chat (no autonomous loop)"""
import asyncio
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, desc
from core.database import get_db, ChatSession, Message
from api.routes.auth import get_current_user

router = APIRouter()

class ChatReq(BaseModel):
    message: str
    session_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    # Node-scoped chat: when set, the session is pinned to this workflow node
    node_id: Optional[str] = None
    workflow_id: Optional[str] = None

@router.get("/sessions")
async def list_sessions(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user.id, ChatSession.node_id == None)  # noqa: E711
        .order_by(desc(ChatSession.updated_at))
        .limit(50)
    )
    sessions = r.scalars().all()
    return [{"id": s.id, "title": s.title, "provider": s.provider,
             "model": s.model, "mode": s.mode, "node_id": s.node_id,
             "workflow_id": s.workflow_id, "updated_at": s.updated_at}
            for s in sessions]

@router.delete("/sessions/{sid}")
async def del_session(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(ChatSession).where(ChatSession.id==sid, ChatSession.user_id==user.id))
    s = r.scalar_one_or_none()
    if not s: from fastapi import HTTPException; raise HTTPException(404)
    await db.delete(s); await db.commit()
    return {"status":"deleted"}

@router.get("/node-session")
async def get_node_session(
    request: Request,
    workflow_id: str,
    node_id: str,
    db=Depends(get_db),
):
    """Get or create a chat session scoped to a specific workflow node."""
    user = await get_current_user(request, db)
    r = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user.id,
            ChatSession.node_id == node_id,
            ChatSession.workflow_id == workflow_id,
        )
    )
    session = r.scalar_one_or_none()
    if not session:
        session = ChatSession(
            user_id=user.id,
            title="Node Chat",
            node_id=node_id,
            workflow_id=workflow_id,
            system_prompt=_build_node_context_prompt(workflow_id, node_id),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return {
        "id": session.id,
        "title": session.title,
        "node_id": session.node_id,
        "workflow_id": session.workflow_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }

@router.get("/sessions/{sid}/messages")
async def get_messages(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Message).where(Message.session_id==sid).order_by(Message.created_at))
    return [{"role":m.role,"content":m.content,"created_at":m.created_at} for m in r.scalars().all()]

class EditReq(BaseModel):
    providerId: Optional[str] = None
    model: Optional[str] = None
    instruction: str
    selectedCode: str = ""
    fullFile: Optional[str] = None
    language: Optional[str] = None


@router.post("/edit")
async def edit(req: EditReq, request: Request, db=Depends(get_db)):
    """Cursor-style CMD+K inline edit — streams back replacement code for
    either the selected snippet or the whole file, per req.selectedCode
    being present or not. Uses the exact same fake-chunking SSE pattern as
    /send above (brain.stream_chat has no true token streaming)."""
    user = await get_current_user(request, db)
    from brain.llm import BrainLLM
    brain = BrainLLM(provider=req.providerId, model=req.model, user_id=user.id)

    lang_hint = f" ({req.language})" if req.language else ""
    if req.selectedCode.strip():
        system = (
            f"You are an expert code editor{lang_hint}. The user selected a "
            "snippet of code and wants it changed per their instruction. "
            "Output ONLY the replacement code for the selected snippet -- "
            "no markdown fences, no explanation, no surrounding context."
        )
        user_msg = f"INSTRUCTION: {req.instruction}\n\nSELECTED CODE:\n{req.selectedCode}"
    else:
        system = (
            f"You are an expert code editor{lang_hint}. The user wants the "
            "whole file changed per their instruction. Output ONLY the "
            "complete new file contents -- no markdown fences, no "
            "explanation."
        )
        user_msg = f"INSTRUCTION: {req.instruction}\n\nFULL FILE:\n{req.fullFile or ''}"

    async def sse():
        try:
            text = await brain.stream_chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ])
            if text.startswith("All providers failed"):
                yield f"data: {json.dumps({'error': text})}\n\n"
                return
            import re as _re
            m = _re.match(r"^```(?:\w+)?\n?(.*?)\n?```$", text.strip(), _re.DOTALL)
            clean = m.group(1) if m else text
            for i in range(0, len(clean), 8):
                yield f"data: {json.dumps({'text': clean[i:i+8]})}\n\n"
                await asyncio.sleep(0.02)
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ExplainReq(BaseModel):
    providerId: Optional[str] = None
    model: Optional[str] = None
    code: str
    language: Optional[str] = None
    filepath: Optional[str] = None


@router.post("/explain")
async def explain(req: ExplainReq, request: Request, db=Depends(get_db)):
    """Streams a plain-English explanation of the given code snippet, using
    the same fake-chunking SSE pattern as /send and /edit."""
    user = await get_current_user(request, db)
    from brain.llm import BrainLLM
    brain = BrainLLM(provider=req.providerId, model=req.model, user_id=user.id)

    lang_hint = f" ({req.language})" if req.language else ""
    system = (
        f"You are an expert software engineer{lang_hint}. Explain the "
        "given code clearly and concisely: what it does, how it works, "
        "and anything notable (edge cases, side effects, complexity). "
        "Use markdown formatting."
    )
    user_msg = f"Explain this code:\n```{req.language or ''}\n{req.code}\n```"

    async def sse():
        try:
            text = await brain.stream_chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ])
            if text.startswith("All providers failed"):
                yield f"data: {json.dumps({'error': text})}\n\n"
                return
            for i in range(0, len(text), 8):
                yield f"data: {json.dumps({'text': text[i:i+8]})}\n\n"
                await asyncio.sleep(0.02)
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/send")
async def send(req: ChatReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    session = None
    # If node_id is provided, look up an existing node-scoped session first
    if req.node_id and req.workflow_id:
        r = await db.execute(
            select(ChatSession).where(
                ChatSession.user_id == user.id,
                ChatSession.node_id == req.node_id,
                ChatSession.workflow_id == req.workflow_id,
            )
        )
        session = r.scalar_one_or_none()
    elif req.session_id:
        r = await db.execute(
            select(ChatSession).where(
                ChatSession.id == req.session_id,
                ChatSession.user_id == user.id,
            )
        )
        session = r.scalar_one_or_none()
    if not session:
        session = ChatSession(
            user_id=user.id, title=req.message[:60],
            provider=req.provider or "ollama", model=req.model or "",
            mode="chat", system_prompt=req.system_prompt,
            node_id=req.node_id,
            workflow_id=req.workflow_id,
        )
        db.add(session); await db.flush()

    # Load history
    r = await db.execute(select(Message).where(Message.session_id==session.id).order_by(Message.created_at).limit(40))
    history = r.scalars().all()
    messages = [{"role":"system","content":req.system_prompt or session.system_prompt or "You are DevOS, a helpful AI assistant."}]
    messages += [{"role":m.role,"content":m.content} for m in history]
    messages.append({"role":"user","content":req.message})

    db.add(Message(session_id=session.id, role="user", content=req.message))
    session.updated_at = datetime.now(timezone.utc)
    if not history: session.title = req.message[:80]
    await db.commit()

    from brain.llm import BrainLLM
    brain = BrainLLM(provider=req.provider or session.provider, model=req.model or session.model or None)

    async def sse():
        full = ""
        try:
            text = await brain.stream_chat(messages)
            # Simulate streaming by chunking
            for i in range(0, len(text), 8):
                chunk = text[i:i+8]
                full += chunk
                yield f"data: {json.dumps({'delta':chunk,'session_id':session.id})}\n\n"
                await asyncio.sleep(0.04)
        except Exception as e:
            full = f"Error: {e}"
            yield f"data: {json.dumps({'delta':full,'session_id':session.id})}\n\n"
        async with db:
            db.add(Message(session_id=session.id, role="assistant", content=full))
            await db.commit()
        yield f"data: {json.dumps({'done':True,'session_id':session.id})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                              headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


def _build_node_context_prompt(workflow_id: str, node_id: str) -> str:
    """Build a system prompt that injects the node's current context into
    the chat so the LLM genuinely knows about this specific node."""
    prompt = (
        f"You are DevOS, an AI assistant. You are currently in a chat scoped "
        f"to workflow node '{node_id}' in workflow '{workflow_id}'. "
        "Answer questions about this node's configuration, inputs, outputs, "
        "and execution history. If asked about other nodes, note that you are "
        "only seeing this node's context."
    )
    return prompt
