"""Personal notes — owner-scoped. RLS defense-in-depth when PostgREST is used."""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from core.database import get_db, Note, gen_id
from api.routes.auth import get_current_user
from governance.tenant_store import ensure_personal_tenant
from core.sanitize import sanitize_freeform

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=50_000)
    tags: List[str] = Field(default_factory=list)

    @field_validator("content", mode="before")
    @classmethod
    def _sanitize(cls, v):
        return sanitize_freeform(v) if isinstance(v, str) else v


def _out(n: Note) -> dict:
    return {
        "id": n.id,
        "content": n.content,
        "tags": list(n.tags or []),
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("")
async def list_notes(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    r = await db.execute(
        select(Note).where(Note.user_id == user.id).order_by(Note.created_at.desc()).limit(200)
    )
    return {"notes": [_out(n) for n in r.scalars().all()]}


@router.post("")
async def create_note(body: NoteCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    content = (body.content or "").strip()[:50_000]
    if not content:
        raise HTTPException(400, detail="content required")
    tags = [str(t)[:64] for t in (body.tags or [])][:20]
    note = Note(id=gen_id(), user_id=user.id, content=content, tags=tags)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _out(note)


@router.get("/{note_id}")
async def get_note(note_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    r = await db.execute(select(Note).where(Note.id == note_id, Note.user_id == user.id))
    note = r.scalar_one_or_none()
    if not note:
        raise HTTPException(404, detail="Note not found")
    return _out(note)


@router.delete("/{note_id}")
async def delete_note(note_id: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    await ensure_personal_tenant(db, user)
    r = await db.execute(select(Note).where(Note.id == note_id, Note.user_id == user.id))
    note = r.scalar_one_or_none()
    if not note:
        raise HTTPException(404, detail="Note not found")
    await db.delete(note)
    await db.commit()
    return {"ok": True}
