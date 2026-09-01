from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
from core.database import get_db
from api.routes.auth import get_current_user
from core.sanitize import sanitize_freeform, sanitize_name

router = APIRouter()

class SaveReq(BaseModel):
    content: str; role: str = "user"
    session_id: Optional[str] = None; metadata: Optional[dict] = None

    # security-audit P3f: memory content is persisted and later replayed
    # back into LLM prompts / rendered in the chat UI, so strip control
    # characters (terminal/log injection, hidden-character smuggling)
    # while preserving legitimate multi-line text.
    @field_validator("content", mode="before")
    @classmethod
    def _sanitize_content(cls, value):
        return sanitize_freeform(value)

class SearchReq(BaseModel):
    query: str; limit: int = 10; session_id: Optional[str] = None

    @field_validator("query", mode="before")
    @classmethod
    def _sanitize_query(cls, value):
        return sanitize_name(value)

@router.post("/save")
async def save(req: SaveReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from memory.store import MemoryStore
    mid = await MemoryStore().save(user.id, req.role, req.content, req.session_id, req.metadata)
    return {"id": mid}

@router.post("/search")
async def search(req: SearchReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from memory.store import MemoryStore
    results = await MemoryStore().recall(user.id, req.query, req.limit, req.session_id)
    return {"results": results, "count": len(results)}

@router.get("/backend")
async def backend():
    from memory.store import MemoryStore
    return {"backend": MemoryStore().backend}


# ── Knowledge graph (Memory: Semantic division, Session 14) ────────────
class AddEntityReq(BaseModel):
    entity_type: str
    name: str
    properties: Optional[dict] = None

    @field_validator("entity_type", "name", mode="before")
    @classmethod
    def _sanitize(cls, value):
        return sanitize_name(value)

class AddRelationshipReq(BaseModel):
    from_entity_id: str
    to_entity_id: str
    relation_type: str
    properties: Optional[dict] = None

    @field_validator("relation_type", mode="before")
    @classmethod
    def _sanitize(cls, value):
        return sanitize_name(value)

@router.post("/graph/entity")
async def add_entity(req: AddEntityReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from memory.graph import KnowledgeGraph
    entity_id = KnowledgeGraph().add_entity(user.id, req.entity_type, req.name, req.properties)
    return KnowledgeGraph().get_entity(entity_id)

@router.get("/graph/entity/{entity_id}")
async def get_entity(entity_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from memory.graph import KnowledgeGraph
    entity = KnowledgeGraph().get_entity(entity_id)
    if not entity:
        raise HTTPException(404, f"No entity '{entity_id}'")
    return entity

@router.get("/graph/entities")
async def find_entities(entity_type: Optional[str] = None, name_contains: Optional[str] = None,
                        request: Request = None, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from memory.graph import KnowledgeGraph
    return {"entities": KnowledgeGraph().find_entities(user.id, entity_type, name_contains)}

@router.post("/graph/relationship")
async def add_relationship(req: AddRelationshipReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from memory.graph import KnowledgeGraph
    try:
        rel_id = KnowledgeGraph().add_relationship(
            user.id, req.from_entity_id, req.to_entity_id, req.relation_type, req.properties)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rel_id}

@router.get("/graph/related/{entity_id}")
async def get_related(entity_id: str, relation_type: Optional[str] = None,
                      direction: str = "both", depth: int = 1,
                      request: Request = None, db=Depends(get_db)):
    await get_current_user(request, db)
    from memory.graph import KnowledgeGraph
    return {"related": KnowledgeGraph().get_related(entity_id, relation_type, direction, depth)}

@router.get("/graph/stats")
async def graph_stats(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from memory.graph import KnowledgeGraph
    return KnowledgeGraph().stats(user.id)
