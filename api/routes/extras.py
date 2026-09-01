"""Project Builder + Agent Library + Workspace + Custom Endpoints + Autoresearch routes."""
import re
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from core.database import get_db
from api.routes.auth import get_current_user

router = APIRouter()

# ── Agent Library ─────────────────────────────────────────────────────────────
@router.get("/agents")
async def list_agents(division: Optional[str] = None, request: Request = None, db=Depends(get_db)):
    from brain.agents import list_agents as _list
    return {"agents": _list(division)}

@router.get("/agents/{slug}")
async def get_agent(slug: str, request: Request = None, db=Depends(get_db)):
    from brain.agents import get_agent as _get
    agent = _get(slug)
    if not agent: raise HTTPException(404)
    return agent.to_dict()

# ── Project Builder ───────────────────────────────────────────────────────────
class BuildRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    stack: str = Field(default="fastapi", min_length=1, max_length=50)
    language: str = Field(default="python", min_length=1, max_length=30)
    features: list[str] = Field(default_factory=list)
    extra_instructions: str = Field(default="", max_length=2000)
    provider: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=80)
    verify: bool = False

    model_config = {"populate_by_name": True}

    @field_validator("name", "description", "extra_instructions", mode="before")
    @classmethod
    def _sanitize_text(cls, value):
        if isinstance(value, str):
            value = re.sub(r"[\x00-\x1f\x7f]", "", value)
            return value.strip()
        return value

    @field_validator("features", mode="before")
    @classmethod
    def _sanitize_features(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [item for item in re.split(r"[,;]+", value) if item.strip()]
        return [re.sub(r"[\x00-\x1f\x7f]", "", str(item)).strip() for item in value if re.sub(r"[\x00-\x1f\x7f]", "", str(item)).strip()]

@router.post("/build")
async def build_project(req: BuildRequest, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.builder import ProjectBuilder, ProjectSpec
    spec = ProjectSpec(name=req.name, description=req.description, stack=req.stack,
                       language=req.language, features=req.features, user_id=user.id,
                       extra_instructions=req.extra_instructions)
    result = await ProjectBuilder(req.provider, req.model).build(spec, verify=req.verify)
    return result.to_dict()

@router.get("/projects")
async def list_projects(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.builder import ProjectBuilder
    return {"projects": ProjectBuilder().list_projects(user.id)}

@router.get("/projects/{pid}/files")
async def project_files(pid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.builder import ProjectBuilder
    files = ProjectBuilder().get_project_files(user.id, pid)
    if not files: raise HTTPException(404)
    return {"files": files}

@router.delete("/projects/{pid}")
async def delete_project(pid: str, request: Request, db=Depends(get_db)):
    """Permanently delete a project — kills any active PTY session for it,
    then removes the project directory from disk. Gated through HITL like
    all other destructive filesystem actions."""
    user = await get_current_user(request, db)

    # Verify project exists before going through HITL
    from brain.builder import ProjectBuilder
    from execution.files import PROJECTS_DIR
    project_dir = (PROJECTS_DIR / user.id / pid).resolve()
    if not project_dir.exists() or not project_dir.is_dir():
        raise HTTPException(404, f"Project not found: {pid}")

    # Route through HITL gate — same trust model as delete_file
    from governance.hitl import HITLQueue
    queue = HITLQueue()
    hitl_req = await queue.submit(
        loop_id="ide-direct", agent_id="human-ide-user", user_id=user.id,
        action="delete_project", action_input=pid,
        description=f"Delete project '{pid}' and all its files",
        cap_required="ucip:filesystem.delete",
        reason="Irreversible: project directory and all files will be permanently removed",
    )
    approved = await queue.wait_for_decision(hitl_req.id)
    if not approved:
        raise HTTPException(403, "Project deletion not approved (denied or timed out)")

    # Kill any active PTY session for this project
    try:
        from execution.pty_session import kill_session
        await kill_session(user.id, pid)
    except Exception:
        pass  # best-effort — session may not exist

    # Remove the project directory
    import shutil
    shutil.rmtree(project_dir, ignore_errors=True)

    return {"deleted": True, "project_id": pid}

@router.get("/stacks")
async def list_stacks():
    from brain.builder import STACK_TEMPLATES
    return {"stacks": [{"id": k, "description": v["description"],
             "language": v["language"], "setup": v["setup"]} for k, v in STACK_TEMPLATES.items()]}

# ── Workspace (AIS-OS inspired) ───────────────────────────────────────────────
class OnboardReq(BaseModel):
    q1: str = ""; q2: str = ""; q3: str = ""; q4: str = ""
    q5: str = ""; q6: str = ""; q7: str = ""

class ConnectionReq(BaseModel):
    name: str; description: str = ""; status: str = "not_connected"
    mechanism: str = ""; api_key_env: str = ""

class DecisionReq(BaseModel):
    decision: str; reasoning: str; alternatives: list[str] = []

    def model_post_init(self, __context):
        if self.alternatives is None:
            self.alternatives = []

@router.get("/workspace")
async def get_workspace(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return Workspace(user.id).to_dict()

@router.post("/workspace/onboard")
async def onboard(req: OnboardReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return await Workspace(user.id).run_onboard(req.model_dump())

@router.get("/workspace/context")
async def workspace_context(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return Workspace(user.id).get_context()

@router.get("/workspace/connections")
async def workspace_connections(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return {"connections": Workspace(user.id).get_connections()}

@router.post("/workspace/connections")
async def add_connection(req: ConnectionReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return Workspace(user.id).add_connection(req.name, req.description, req.status, req.mechanism, req.api_key_env)

@router.post("/workspace/audit")
async def run_audit(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return await Workspace(user.id).run_audit()

@router.post("/workspace/level-up")
async def run_level_up(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return await Workspace(user.id).run_level_up()

@router.get("/workspace/decisions")
async def get_decisions(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return {"decisions": Workspace(user.id).get_decisions()}

@router.post("/workspace/decisions")
async def log_decision(req: DecisionReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from workspace.workspace import Workspace
    return Workspace(user.id).log_decision(req.decision, req.reasoning, req.alternatives)

# ── Custom Endpoints ──────────────────────────────────────────────────────────
class EndpointCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    base_url: str = Field(..., min_length=1, max_length=400)
    api_key: str = Field(default="", max_length=400)
    api_format: str = Field(default="openai", max_length=40)
    default_model: str = Field(default="", max_length=200)

    @field_validator("name", "base_url", "api_format", "default_model", "api_key", mode="before")
    @classmethod
    def _sanitize_text(cls, value):
        if isinstance(value, str):
            value = re.sub(r"[\x00-\x1f\x7f]", "", value)
            return value.strip()
        return value

@router.get("/endpoints")
async def list_endpoints(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.endpoints import EndpointRegistry
    return {"endpoints": [e.to_dict() for e in EndpointRegistry().list_for_user(user.id)]}

@router.post("/endpoints")
async def add_endpoint(req: EndpointCreate, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.endpoints import EndpointRegistry
    return {"id": EndpointRegistry().add(user.id, req.name, req.base_url, req.api_key, req.api_format, req.default_model)}

@router.post("/endpoints/{eid}/test")
async def test_endpoint(eid: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from brain.endpoints import EndpointRegistry, CustomEndpointClient
    ep = EndpointRegistry().get(eid)
    if not ep: raise HTTPException(404)
    client = CustomEndpointClient(ep)
    try: ok, msg = await client.test_connection(); return {"success": ok, "message": msg}
    finally: await client.close()

@router.get("/endpoints/{eid}/models")
async def endpoint_models(eid: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from brain.endpoints import EndpointRegistry, CustomEndpointClient
    ep = EndpointRegistry().get(eid)
    if not ep: raise HTTPException(404)
    client = CustomEndpointClient(ep)
    try: return {"models": await client.list_models()}
    finally: await client.close()

@router.delete("/endpoints/{eid}")
async def delete_endpoint(eid: str, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.endpoints import EndpointRegistry
    if not EndpointRegistry().delete(eid, user.id): raise HTTPException(404)
    return {"status": "deleted"}

# ── Autoresearch ──────────────────────────────────────────────────────────────
class AutoresearchReq(BaseModel):
    goal: str; code: str; max_rounds: int = 10

@router.post("/autoresearch/start")
async def start_autoresearch(req: AutoresearchReq, request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    from brain.autoresearch import AutoresearchSession
    session = AutoresearchSession(user_id=user.id, max_rounds=req.max_rounds)
    goal = await session.parse_goal(req.goal, req.code)
    return await session.run(goal)

@router.get("/autoresearch/sessions")
async def list_autoresearch(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    from brain.autoresearch import AutoresearchSession
    return {"sessions": AutoresearchSession.list_sessions()}
