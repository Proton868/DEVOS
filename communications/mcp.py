"""
Communications — MCP Transport (Agency OS Master Plan §5).

Implements the Model Context Protocol (MCP) server and client for tool
exposure and discovery. Allows DevOS to:
1. **Serve**: Expose its capabilities as MCP tools that external clients
   (Claude Desktop, Cursor, etc.) can discover and invoke.
2. **Consume**: Connect to external MCP servers to discover and use their
   tools, expanding DevOS's capability set at runtime.

This is the MCP transport layer — it sits alongside the existing EventBus
(communications/bus.py) and SSE streaming (api/routes/comms.py) as the
third communications channel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("devos.communications.mcp")


# ── MCP Protocol Types ─────────────────────────────────────────────────────────

@dataclass
class MCPTool:
    """An MCP tool definition — what DevOS exposes to external clients."""
    name: str
    description: str
    input_schema: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": [],
    })

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class MCPResource:
    """An MCP resource — data that external clients can read."""
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"

    def to_dict(self) -> dict:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


# ── MCP Server ─────────────────────────────────────────────────────────────────

class MCPServer:
    """Exposes DevOS capabilities as MCP tools over stdio (JSON-RPC).

    This is the server-side: external clients (Claude Desktop, Cursor, etc.)
    connect to this via stdio and discover/invoke DevOS capabilities.

    Usage:
        server = MCPServer()
        server.register_tool("search_web", "Search the web", {...}, handler)
        await server.run()  # blocks on stdio
    """

    def __init__(self, name: str = "devos", version: str = "1.0.0"):
        self.name = name
        self.version = version
        self._tools: dict[str, MCPTool] = {}
        self._handlers: dict[str, Callable[..., Awaitable[dict]]] = {}
        self._resources: list[MCPResource] = []

    def register_tool(self, name: str, description: str,
                      input_schema: Optional[dict] = None,
                      handler: Optional[Callable[..., Awaitable[dict]]] = None):
        """Register a tool that external clients can invoke."""
        tool = MCPTool(name=name, description=description,
                       input_schema=input_schema or {
                           "type": "object",
                           "properties": {},
                           "required": [],
                       })
        self._tools[name] = tool
        if handler:
            self._handlers[name] = handler

    def register_resource(self, uri: str, name: str,
                          description: str = "",
                          mime_type: str = "text/plain"):
        """Register a resource that external clients can read."""
        self._resources.append(MCPResource(
            uri=uri, name=name, description=description, mime_type=mime_type,
        ))

    def _build_list_tools_response(self) -> dict:
        return {
            "tools": [t.to_dict() for t in self._tools.values()],
        }

    def _build_list_resources_response(self) -> dict:
        return {
            "resources": [r.to_dict() for r in self._resources],
        }

    async def _handle_request(self, request: dict) -> dict:
        """Handle a single JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {
                            "name": self.name,
                            "version": self.version,
                        },
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                        },
                    },
                }

            elif method == "notifications/initialized":
                return None  # No response for notifications

            elif method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": self._build_list_tools_response(),
                }

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})

                if tool_name not in self._handlers:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Tool not found: {tool_name}",
                        },
                    }

                handler = self._handlers[tool_name]
                result = await handler(**arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result)},
                        ],
                    },
                }

            elif method == "resources/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": self._build_list_resources_response(),
                }

            elif method == "ping":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {},
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }

        except Exception as e:
            logger.error(f"[mcp] error handling {method}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": str(e),
                },
            }

    async def run(self):
        """Run the MCP server on stdio. Blocks until stdin closes."""
        logger.info(f"[mcp] server starting: {self.name} v{self.version}")
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(
            lambda: protocol, sys.stdin
        )

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            lambda: asyncio.streams.FlowControlMixin(), sys.stdout
        )
        writer = asyncio.StreamWriter(
            writer_transport, writer_protocol, reader, asyncio.get_event_loop()
        )

        buffer = ""
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                request = json.loads(line_str)
                response = await self._handle_request(request)

                if response is not None:
                    response_str = json.dumps(response) + "\n"
                    writer.write(response_str.encode("utf-8"))
                    await writer.drain()

            except json.JSONDecodeError:
                logger.warning(f"[mcp] invalid JSON received: {line_str[:100]}")
            except Exception as e:
                logger.error(f"[mcp] unexpected error: {e}")

        logger.info("[mcp] server stopped")


def create_devos_mcp_server() -> MCPServer:
    """Create an MCP server pre-loaded with all DevOS capabilities."""
    server = MCPServer(name="devos", version="4.0.0")

    # Register core tools
    server.register_tool(
        "search_web",
        "Search the web for information using Tavily or SearXNG",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results (1-10)", "default": 5},
            },
            "required": ["query"],
        },
    )

    server.register_tool(
        "write_file",
        "Write content to a file in the workspace",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    )

    server.register_tool(
        "read_file",
        "Read content from a file in the workspace",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
            },
            "required": ["path"],
        },
    )

    server.register_tool(
        "run_code",
        "Execute code in a sandboxed environment",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Code to execute"},
                "language": {"type": "string", "description": "python | bash | node", "default": "python"},
            },
            "required": ["code"],
        },
    )

    server.register_tool(
        "deep_research",
        "Conduct multi-step web research with citations",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Research question"},
                "max_sources": {"type": "integer", "description": "Max sources", "default": 5},
                "depth": {"type": "string", "description": "quick | standard | deep", "default": "standard"},
            },
            "required": ["question"],
        },
    )

    server.register_tool(
        "deploy_workflow",
        "Deploy a workflow to the execution engine",
        {
            "type": "object",
            "properties": {
                "workflow_yaml": {"type": "string", "description": "Workflow in YAML format"},
            },
            "required": ["workflow_yaml"],
        },
    )

    # Register resources
    server.register_resource(
        "devos://capabilities", "Capability Registry",
        "List of all registered UCIP capabilities",
    )
    server.register_resource(
        "devos://workers", "Worker Library",
        "List of all available worker personas",
    )
    server.register_resource(
        "devos://memory/semantic", "Semantic Memory",
        "Knowledge graph entities and relationships",
    )

    return server


# ── MCP Client ─────────────────────────────────────────────────────────────────

class MCPClient:
    """Connects to an external MCP server over stdio and discovers/invokes
    its tools. This allows DevOS to consume external MCP servers at runtime,
    expanding the available capability set.

    Usage:
        client = MCPClient()
        await client.connect(["python", "-m", "some_mcp_server"])
        tools = await client.list_tools()
        result = await client.call_tool("some_tool", {"arg": "value"})
        await client.disconnect()
    """

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._tools: list[dict] = []
        self._resources: list[dict] = []
        self._server_info: dict = {}
        self._connected = False
        self._request_id = 0

    async def connect(self, command: list[str]) -> dict:
        """Connect to an MCP server by spawning its process."""
        logger.info(f"[mcp] connecting to: {' '.join(command)}")
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Initialize
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "devos", "version": "4.0.0"},
            "capabilities": {},
        })
        self._server_info = result
        self._connected = True

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        # Discover tools
        tools_result = await self._send_request("tools/list", {})
        self._tools = tools_result.get("tools", [])

        # Discover resources
        try:
            resources_result = await self._send_request("resources/list", {})
            self._resources = resources_result.get("resources", [])
        except Exception:
            self._resources = []

        logger.info(
            f"[mcp] connected to {self._server_info.get('serverInfo', {}).get('name', 'unknown')} "
            f"({len(self._tools)} tools, {len(self._resources)} resources)"
        )
        return self._server_info

    async def disconnect(self):
        """Disconnect from the MCP server."""
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
        self._connected = False
        self._tools = []
        self._resources = []

    async def list_tools(self) -> list[dict]:
        """List tools available on the connected server."""
        if not self._connected:
            raise RuntimeError("Not connected to an MCP server")
        return self._tools

    async def call_tool(self, name: str, arguments: dict = None) -> dict:
        """Call a tool on the connected server."""
        if not self._connected:
            raise RuntimeError("Not connected to an MCP server")
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        return result

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and return the result."""
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        return await self._send_raw(request)

    async def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._send_raw(request, expect_response=False)

    async def _send_raw(self, request: dict, expect_response: bool = True) -> Optional[dict]:
        """Send raw JSON-RPC and optionally wait for response."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP process not available")

        request_str = json.dumps(request) + "\n"
        self._process.stdin.write(request_str.encode("utf-8"))
        await self._process.stdin.drain()

        if not expect_response:
            return None

        # Read response line
        response_line = await asyncio.wait_for(
            self._process.stdout.readline(), timeout=30
        )
        if not response_line:
            raise RuntimeError("MCP server closed connection")

        response = json.loads(response_line.decode("utf-8").strip())
        if "error" in response:
            raise RuntimeError(
                f"MCP error: {response['error'].get('message', 'unknown')}"
            )
        return response.get("result", {})


# ── MCP Discovery Service ──────────────────────────────────────────────────────

class MCPDiscoveryService:
    """Manages multiple MCP client connections, providing a unified tool
    registry that merges DevOS's native capabilities with tools discovered
    from external MCP servers.

    This is the bridge between the MCP transport layer and the rest of
    DevOS — the CapabilityRegistry can query this service to discover
    new tools at runtime, and the BrainExecutionLoop can use them as if
    they were native UCIP capabilities.
    """

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}
        self._all_tools: list[dict] = []
        self._tool_to_server: dict[str, str] = {}

    async def connect_server(self, name: str, command: list[str]):
        """Connect to an external MCP server and ingest its tools."""
        if name in self._clients:
            await self._clients[name].disconnect()

        client = MCPClient()
        await client.connect(command)
        self._clients[name] = client

        tools = await client.list_tools()
        for tool in tools:
            tool_name = tool.get("name", "")
            # Prefix with server name to avoid collisions
            prefixed = f"mcp:{name}:{tool_name}"
            tool["_prefixed_name"] = prefixed
            tool["_server"] = name
            self._all_tools.append(tool)
            self._tool_to_server[prefixed] = name

        logger.info(
            f"[mcp] discovery: {name} → {len(tools)} tools ingested"
        )

    async def disconnect_server(self, name: str):
        """Disconnect from an MCP server and remove its tools."""
        if name in self._clients:
            await self._clients[name].disconnect()
            del self._clients[name]

        self._all_tools = [
            t for t in self._all_tools if t.get("_server") != name
        ]
        self._tool_to_server = {
            k: v for k, v in self._tool_to_server.items() if v != name
        }

    async def call_tool(self, prefixed_name: str, arguments: dict = None) -> dict:
        """Call a tool from any connected MCP server."""
        server_name = self._tool_to_server.get(prefixed_name)
        if not server_name or server_name not in self._clients:
            raise RuntimeError(f"Tool not found: {prefixed_name}")

        # Strip the prefix to get the original tool name
        original_name = prefixed_name[len(f"mcp:{server_name}:"):]
        return await self._clients[server_name].call_tool(
            original_name, arguments
        )

    def list_all_tools(self) -> list[dict]:
        """List all tools from all connected servers."""
        return self._all_tools

    async def disconnect_all(self):
        """Disconnect from all servers."""
        for name in list(self._clients.keys()):
            await self.disconnect_server(name)


# Module-level singleton
_discovery = MCPDiscoveryService()


def get_mcp_discovery() -> MCPDiscoveryService:
    return _discovery