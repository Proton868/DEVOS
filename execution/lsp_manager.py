"""
DEVOS Language Server Manager.

Monaco (useLSP) → WebSocket → this manager → external LSP process (stdio).

Does NOT implement language analysis inside DEVOS. It launches and proxies
external language servers, confined to the project workspace root.

Security:
- Working directory and rootUri are always under PROJECTS_DIR/{user}/{project}
- Client virtual URIs file:///workspace/... are translated to real paths
- Path traversal / escape outside project root is rejected
- Servers that are not installed simply report unavailable (no silent host fallback)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

from execution.files import PROJECTS_DIR

logger = logging.getLogger("devos.lsp")

WORKSPACE_URI_PREFIX = "file:///workspace/"

# language id → candidate argv lists (first available wins)
SERVER_COMMANDS: dict[str, list[list[str]]] = {
    "python": [
        ["pylsp"],
        ["python3", "-m", "pylsp"],
        ["pyright-langserver", "--stdio"],
        ["python3", "-m", "pyright", "--stdio"],
    ],
    "typescript": [
        ["typescript-language-server", "--stdio"],
    ],
    "javascript": [
        ["typescript-language-server", "--stdio"],
    ],
    "json": [
        ["vscode-json-language-server", "--stdio"],
        ["vscode-json-languageserver", "--stdio"],
    ],
    "yaml": [
        ["yaml-language-server", "--stdio"],
    ],
    "html": [
        ["vscode-html-language-server", "--stdio"],
        ["vscode-html-languageserver", "--stdio"],
    ],
    "css": [
        ["vscode-css-language-server", "--stdio"],
        ["vscode-css-languageserver", "--stdio"],
    ],
}

SUPPORTED_LANGUAGES = set(SERVER_COMMANDS.keys())


def resolve_server_command(language: str) -> Optional[list[str]]:
    for argv in SERVER_COMMANDS.get(language, []):
        bin_name = argv[0]
        if shutil.which(bin_name):
            return list(argv)
        # python -m form
        if bin_name in ("python3", "python") and len(argv) >= 3:
            return list(argv)
    return None


def list_available_servers() -> dict[str, dict]:
    out = {}
    for lang in sorted(SUPPORTED_LANGUAGES):
        cmd = resolve_server_command(lang)
        out[lang] = {
            "language": lang,
            "available": cmd is not None,
            "command": cmd,
        }
    return out


def project_root(user_id: str, project_id: str) -> Path:
    root = (PROJECTS_DIR / user_id / project_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def virtual_to_real(uri: str, root: Path) -> Optional[Path]:
    """Map file:///workspace/rel → root/rel. Reject escapes."""
    if not uri:
        return None
    if uri.startswith(WORKSPACE_URI_PREFIX):
        rel = unquote(uri[len(WORKSPACE_URI_PREFIX):]).lstrip("/")
    elif uri.startswith("file://"):
        parsed = urlparse(uri)
        path = unquote(parsed.path)
        # If already under root, accept
        try:
            p = Path(path).resolve()
            p.relative_to(root)
            return p
        except Exception:
            return None
    else:
        return None
    try:
        real = (root / rel).resolve()
        real.relative_to(root)
        return real
    except Exception:
        return None


def real_to_virtual(path: Path, root: Path) -> Optional[str]:
    try:
        rel = path.resolve().relative_to(root.resolve())
        return WORKSPACE_URI_PREFIX + rel.as_posix()
    except Exception:
        return None


def rewrite_uris_in_obj(obj: Any, transform: Callable[[str], Optional[str]]) -> Any:
    """Deep-rewrite string values that look like file URIs."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("uri", "rootUri", "targetUri", "newUri", "oldUri") and isinstance(v, str):
                nv = transform(v)
                out[k] = nv if nv is not None else v
            elif k == "workspaceFolders" and isinstance(v, list):
                out[k] = rewrite_uris_in_obj(v, transform)
            else:
                out[k] = rewrite_uris_in_obj(v, transform)
        return out
    if isinstance(obj, list):
        return [rewrite_uris_in_obj(x, transform) for x in obj]
    return obj


@dataclass
class LanguageServerSession:
    user_id: str
    project_id: str
    language: str
    root: Path
    command: list[str]
    proc: Optional[asyncio.subprocess.Process] = None
    _reader_task: Optional[asyncio.Task] = None
    _initialized: bool = False
    _clients: list = field(default_factory=list)  # callables or websockets
    _pending_client: dict[int, Any] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _buffer: bytes = b""
    _next_id: int = 1
    _server_init_id: Optional[int] = None

    @property
    def key(self) -> tuple:
        return (self.user_id, self.project_id, self.language)

    async def start(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root),
            env={
                **os.environ,
                # Reduce accidental network from some servers; not a full sandbox
                "NO_UPDATE_NOTIFIER": "1",
            },
            limit=8 * 1024 * 1024,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._send_initialize()
        logger.info(
            "LSP started lang=%s user=%s project=%s cmd=%s",
            self.language, self.user_id, self.project_id, self.command,
        )

    async def stop(self) -> None:
        try:
            if self.proc and self.proc.returncode is None:
                await self._write_message({"jsonrpc": "2.0", "method": "shutdown", "id": self._alloc_id()})
                await asyncio.sleep(0.1)
                await self._write_message({"jsonrpc": "2.0", "method": "exit"})
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    self.proc.kill()
        except Exception:
            if self.proc and self.proc.returncode is None:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except Exception:
                pass
        self.proc = None
        self._initialized = False

    def _alloc_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    async def _send_initialize(self) -> None:
        root_uri = self.root.as_uri()
        init_id = self._alloc_id()
        self._server_init_id = init_id
        params = {
            "processId": os.getpid(),
            "clientInfo": {"name": "DEVOS", "version": "1.0"},
            "rootPath": str(self.root),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didOpen": True, "didChange": True, "didClose": True},
                    "completion": {"completionItem": {"snippetSupport": False}},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {"linkSupport": True},
                    "references": {},
                    "documentSymbol": {},
                    "publishDiagnostics": {},
                    "rename": {},
                    "signatureHelp": {},
                    "codeAction": {},
                },
                "workspace": {
                    "workspaceFolders": True,
                },
            },
            "workspaceFolders": [
                {"uri": root_uri, "name": self.project_id},
            ],
            "trace": "off",
        }
        await self._write_message({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": params,
        })

    async def _write_message(self, msg: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("language server not running")
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self.proc.stdin.write(header + body)
        await self.proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1].strip())
                    # consume rest of headers
                    while True:
                        hdr = await self.proc.stdout.readline()
                        if hdr in (b"\r\n", b"\n", b""):
                            break
                    body = await self.proc.stdout.readexactly(length)
                    try:
                        msg = json.loads(body.decode("utf-8"))
                    except Exception:
                        continue
                    await self._on_server_message(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LSP read loop error lang=%s", self.language)

    async def _on_server_message(self, msg: dict) -> None:
        # Handle initialize result
        if msg.get("id") == self._server_init_id and "result" in msg:
            self._initialized = True
            try:
                await self._write_message({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            except Exception:
                pass
            return

        # Rewrite real URIs → virtual for client
        def to_virtual(uri: str) -> Optional[str]:
            if uri.startswith(WORKSPACE_URI_PREFIX):
                return uri
            if uri.startswith("file://"):
                parsed = urlparse(uri)
                path = Path(unquote(parsed.path)).resolve()
                return real_to_virtual(path, self.root)
            return uri

        client_msg = rewrite_uris_in_obj(msg, to_virtual)
        # Fan-out to all attached client send callbacks
        dead = []
        for send in list(self._clients):
            try:
                await send(client_msg)
            except Exception:
                dead.append(send)
        for d in dead:
            try:
                self._clients.remove(d)
            except ValueError:
                pass

    def attach(self, send_coro_factory) -> None:
        """send_coro_factory: async callable(msg: dict)."""
        if send_coro_factory not in self._clients:
            self._clients.append(send_coro_factory)

    def detach(self, send_coro_factory) -> None:
        try:
            self._clients.remove(send_coro_factory)
        except ValueError:
            pass

    async def forward_from_client(self, msg: dict) -> None:
        """Accept client JSON-RPC, rewrite URIs, forward to server."""
        if not self.proc or self.proc.returncode is not None:
            await self.start()

        def to_real(uri: str) -> Optional[str]:
            if uri.startswith(WORKSPACE_URI_PREFIX) or uri.startswith("file://"):
                real = virtual_to_real(uri, self.root)
                if real is None:
                    return None
                return real.as_uri()
            return uri

        # Block initialize from client — we own server lifecycle
        method = msg.get("method")
        if method == "initialize":
            # Respond locally with minimal capabilities so Monaco can proceed
            # Actual server already initialized by manager
            if "id" in msg:
                await self._reply_client_initialize(msg)
            return
        if method == "initialized":
            return
        if method == "shutdown":
            if "id" in msg:
                # reply on clients
                for send in list(self._clients):
                    try:
                        await send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
                    except Exception:
                        pass
            return

        server_msg = rewrite_uris_in_obj(msg, to_real)
        # If any uri rewrite failed for document paths, reject
        if method and method.startswith("textDocument/"):
            params = msg.get("params") or {}
            doc = params.get("textDocument") or {}
            uri = doc.get("uri")
            if uri and virtual_to_real(uri, self.root) is None:
                if "id" in msg:
                    for send in list(self._clients):
                        try:
                            await send({
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {"code": -32602, "message": "path outside workspace"},
                            })
                        except Exception:
                            pass
                return

        await self._write_message(server_msg)

    async def _reply_client_initialize(self, msg: dict) -> None:
        result = {
            "capabilities": {
                "textDocumentSync": 1,
                "hoverProvider": True,
                "definitionProvider": True,
                "referencesProvider": True,
                "documentSymbolProvider": True,
                "workspaceSymbolProvider": True,
                "completionProvider": {"triggerCharacters": [".", '"', "'"]},
                "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                "renameProvider": True,
                "codeActionProvider": True,
            },
            "serverInfo": {"name": f"devos-lsp-proxy/{self.language}"},
        }
        for send in list(self._clients):
            try:
                await send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            except Exception:
                pass


class LspManager:
    def __init__(self) -> None:
        self._sessions: dict[tuple, LanguageServerSession] = {}
        self._lock = asyncio.Lock()

    async def get_session(
        self, user_id: str, project_id: str, language: str
    ) -> LanguageServerSession:
        language = (language or "").lower().strip()
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language: {language}")
        cmd = resolve_server_command(language)
        if not cmd:
            raise FileNotFoundError(
                f"no language server installed for {language}; "
                f"tried {[c[0] for c in SERVER_COMMANDS.get(language, [])]}"
            )
        key = (user_id, project_id, language)
        async with self._lock:
            sess = self._sessions.get(key)
            if sess and sess.proc and sess.proc.returncode is None:
                return sess
            root = project_root(user_id, project_id)
            sess = LanguageServerSession(
                user_id=user_id,
                project_id=project_id,
                language=language,
                root=root,
                command=cmd,
            )
            await sess.start()
            self._sessions[key] = sess
            return sess

    async def stop_session(self, user_id: str, project_id: str, language: str) -> None:
        key = (user_id, project_id, language)
        async with self._lock:
            sess = self._sessions.pop(key, None)
        if sess:
            await sess.stop()

    async def stop_all_for_user(self, user_id: str) -> None:
        async with self._lock:
            keys = [k for k in self._sessions if k[0] == user_id]
            sessions = [self._sessions.pop(k) for k in keys]
        for s in sessions:
            await s.stop()


# Process-wide singleton
_MANAGER: Optional[LspManager] = None


def get_lsp_manager() -> LspManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = LspManager()
    return _MANAGER
