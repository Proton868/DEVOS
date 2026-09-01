"""MCP (Model Context Protocol) route — lets the frontend connect DevOS
to external MCP servers (Supabase, filesystem, GitHub, or any custom
stdio-based MCP server), discover their tools, and invoke them.

Wraps the existing `communications.mcp.MCPDiscoveryService` singleton in a
thin, authenticated HTTP surface. No new transport logic lives here — this
file is purely the API boundary."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from core.database import get_db
from api.routes.auth import get_current_user

logger = logging.getLogger("devos.mcp")
router = APIRouter()


class ConnectReq(BaseModel):
    name: str
    command: list[str]


class CallToolReq(BaseModel):
    name: str
    arguments: dict = {}


# Known-good presets for one-click connection. Each preset's command is a
# stdio-launchable MCP server; anything marked "requires_env" needs those
# environment variables set in .env before it will actually work, but the
# preset itself is always listed so the UI can show what's possible.
MCP_PRESETS = [
    {
        "id": "supabase",
        "label": "Supabase",
        "description": "Query and manage your Supabase project (tables, RLS, storage) via MCP.",
        "command": ["npx", "-y", "@supabase/mcp-server-supabase@latest"],
        "requires_env": ["SUPABASE_URL", "SUPABASE_KEY"],
        "icon": "database",
    },
    {
        "id": "filesystem",
        "label": "Filesystem",
        "description": "Give the agent MCP-standard read/write access to a local directory.",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
        "requires_env": [],
        "icon": "folder",
    },
    {
        "id": "github",
        "label": "GitHub",
        "description": "Search repos, read files, open issues/PRs via the official GitHub MCP server.",
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "requires_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "icon": "github",
    },
    {
        "id": "postgres",
        "label": "Postgres",
        "description": "Read-only schema-aware SQL access to a Postgres database.",
        "command": ["npx", "-y", "@modelcontextprotocol/server-postgres"],
        "requires_env": ["DATABASE_URL"],
        "icon": "database",
    },
]


def _discovery():
    from communications.mcp import get_mcp_discovery
    return get_mcp_discovery()


@router.get("/presets")
async def list_presets(request: Request, db=Depends(get_db)):
    """Known one-click MCP server presets the UI can offer."""
    await get_current_user(request, db)
    from core.config import settings
    presets = []
    for p in MCP_PRESETS:
        missing = [e for e in p["requires_env"] if not getattr(settings, e, "")]
        presets.append({**p, "ready": len(missing) == 0, "missing_env": missing})
    return {"presets": presets}


@router.get("/servers")
async def list_servers(request: Request, db=Depends(get_db)):
    """List currently connected MCP servers and how many tools each ingested."""
    await get_current_user(request, db)
    disc = _discovery()
    counts: dict[str, int] = {}
    for t in disc.list_all_tools():
        s = t.get("_server", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return {
        "servers": [
            {"name": name, "tool_count": counts.get(name, 0), "connected": True}
            for name in disc._clients.keys()
        ]
    }


@router.post("/connect")
async def connect_server(req: ConnectReq, request: Request, db=Depends(get_db)):
    """Connect to an external MCP server by spawning its stdio process and
    ingesting its tool list into the unified registry."""
    user = await get_current_user(request, db)
    if not req.name or not req.command:
        raise HTTPException(400, "name and command are required")
    disc = _discovery()
    try:
        await disc.connect_server(req.name, req.command)
    except Exception as e:
        logger.exception("MCP connect failed for %s", req.name)
        raise HTTPException(502, f"Failed to connect to MCP server '{req.name}': {e}")

    tools = [t for t in disc.list_all_tools() if t.get("_server") == req.name]
    return {"connected": True, "name": req.name, "tool_count": len(tools), "tools": tools}


@router.post("/disconnect/{name}")
async def disconnect_server(name: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    disc = _discovery()
    if name not in disc._clients:
        raise HTTPException(404, f"MCP server '{name}' is not connected")
    await disc.disconnect_server(name)
    return {"disconnected": True, "name": name}


@router.get("/tools")
async def list_tools(request: Request, db=Depends(get_db)):
    """All tools discovered across all connected MCP servers."""
    await get_current_user(request, db)
    return {"tools": _discovery().list_all_tools()}


@router.post("/call")
async def call_tool(req: CallToolReq, request: Request, db=Depends(get_db)):
    """Invoke a tool exposed by any connected MCP server. Tool names are
    prefixed as `mcp:<server>:<tool>`."""
    await get_current_user(request, db)
    disc = _discovery()
    try:
        result = await disc.call_tool(req.name, req.arguments)
    except RuntimeError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("MCP tool call failed: %s", req.name)
        raise HTTPException(502, f"Tool call failed: {e}")
    return {"result": result}
