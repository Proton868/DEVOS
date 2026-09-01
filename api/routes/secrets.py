"""Secrets route — encrypted credential storage for Flow scripts.
Closes a real gap found in record.md Session 22: FlowPanel.jsx expected a
/secrets API that never existed anywhere on the backend, and
ExecutionLayer.run() already accepted a `secrets` dict that nothing ever
populated. List/get never return the decrypted value — only name/
description/timestamps — decryption only happens server-side at script
execution time (see api/routes/scripts.py's run_script)."""
import re
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
from sqlalchemy import select
from core.database import get_db, Secret
from api.routes.auth import get_current_user
from governance.secrets_vault import encrypt
from core.sanitize import sanitize_freeform

router = APIRouter()


def normalize_secret_name(name: str) -> str:
    value = (name or "").strip().upper()
    value = re.sub(r"[^A-Z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        raise ValueError("Secret name is required")
    if value.startswith("SECRET_"):
        value = value[len("SECRET_"):]
    if not value:
        raise ValueError("Secret name is required")
    return value


class SecretCreate(BaseModel):
    name: str
    value: str
    description: Optional[str] = None

    # security-audit P3f: description is persisted and rendered in the
    # Secrets UI; `name` is separately normalized by normalize_secret_name()
    # and `value` is encrypted before storage, so neither needs this.
    @field_validator("description", mode="before")
    @classmethod
    def _sanitize_description(cls, value):
        return sanitize_freeform(value)


def _to_dict(s: Secret) -> dict:
    # Deliberately no `value`/`encrypted_value` field here — the list/get
    # views never expose the secret's contents, encrypted or not.
    return {"id": s.id, "name": s.name, "description": s.description,
            "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat()}


@router.get("")
async def list_secrets(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Secret).where(Secret.owner_id == user.id))
    return {"secrets": [_to_dict(s) for s in r.scalars().all()]}


@router.post("")
async def create_secret(req: SecretCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    if not req.value:
        raise HTTPException(400, "Both name and value are required")
    try:
        secret_name = normalize_secret_name(req.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    secret = Secret(owner_id=user.id, name=secret_name,
                    description=req.description, encrypted_value=encrypt(req.value))
    db.add(secret)
    await db.commit()
    await db.refresh(secret)
    return _to_dict(secret)


@router.delete("/{sid}")
async def delete_secret(sid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    r = await db.execute(select(Secret).where(Secret.id == sid, Secret.owner_id == user.id))
    s = r.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Secret not found")
    await db.delete(s)
    await db.commit()
    return {"status": "deleted"}
