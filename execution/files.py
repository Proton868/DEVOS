"""
Execution Layer — File Service.
Does NOT decide what to write. Does NOT reason about content.
Just performs filesystem operations faithfully, scoped to a project root,
and returns structured output to the Brain / API layer.

Every project lives at data/projects/{user_id}/{project_id}/ — the same
convention brain/builder.py already uses, so the IDE and the Project
Builder operate on the same files instead of two separate trees.
"""
import logging
import shutil
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("devos.files")

PROJECTS_DIR = Path("data/projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_READ_BYTES = 2_000_000  # 2MB safety cap for inline read/write over the API
BINARY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                      ".ttf", ".eot", ".pdf", ".zip", ".gz", ".exe", ".bin", ".so", ".pyc"}


class PathViolation(Exception):
    """Raised when a requested path would escape the project root."""


class FileService:
    def __init__(self, user_id: str, project_id: str):
        self.root = (PROJECTS_DIR / user_id / project_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a relative path inside the project root; refuse escapes.

        Canonicalize before authorization: resolve() follows symlinks, then
        we verify the final target still lives under the project root.
        """
        raw = (rel_path or "").replace("\\", "/")
        # Reject absolute and null-byte paths BEFORE stripping leading slashes
        if "\x00" in raw:
            raise PathViolation(f"Invalid path: {raw!r}")
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            raise PathViolation(f"Absolute path refused: {raw!r}")
        rel_path = raw.lstrip("/")
        if not rel_path:
            return self.root
        candidate = (self.root / rel_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise PathViolation(f"Path escapes project root: {rel_path}")
        return candidate

    def tree(self, max_depth: int | None = None) -> list[dict]:
        """Hierarchical tree under the project root.

        max_depth=None → full tree (bounded by MAX_TREE_ENTRIES).
        max_depth=1    → root listing only (lazy expand uses list_dir).
        Excludes .git internals.
        """
        MAX_TREE_ENTRIES = 5000
        return self._build_tree(
            self.root, depth=0, max_depth=max_depth, budget=[MAX_TREE_ENTRIES]
        )

    def list_dir(self, rel_path: str = "") -> list[dict]:
        """Lazy one-level listing of a directory (IDE expand-on-demand)."""
        p = self._resolve(rel_path) if rel_path else self.root
        if not p.exists() or not p.is_dir():
            raise FileNotFoundError(rel_path or ".")
        items: list[dict] = []
        try:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError as e:
            raise PathViolation(f"Cannot list {rel_path}: {e}") from e
        for child in entries:
            rel = str(child.relative_to(self.root))
            if rel == ".git" or rel.startswith(".git/"):
                continue
            is_dir = child.is_dir()
            items.append({
                "path": rel,
                "name": child.name,
                "type": "directory" if is_dir else "file",
                "size": child.stat().st_size if child.is_file() else None,
                "is_binary": (not is_dir) and child.suffix.lower() in BINARY_EXTENSIONS,
                "children": [] if is_dir else None,
                "lazy": is_dir,
            })
        return items

    def _build_tree(
        self,
        dir_path: Path,
        depth: int,
        max_depth: int | None,
        budget: list[int],
    ) -> list[dict]:
        if budget[0] <= 0:
            return []
        if max_depth is not None and depth >= max_depth:
            return []
        nodes: list[dict] = []
        try:
            entries = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return []
        for child in entries:
            if budget[0] <= 0:
                break
            rel = str(child.relative_to(self.root))
            if rel == ".git" or rel.startswith(".git/"):
                continue
            budget[0] -= 1
            is_dir = child.is_dir()
            node: dict = {
                "path": rel,
                "name": child.name,
                "type": "directory" if is_dir else "file",
                "size": child.stat().st_size if child.is_file() else None,
                "is_binary": (not is_dir) and child.suffix.lower() in BINARY_EXTENSIONS,
            }
            if is_dir:
                if max_depth is None or depth + 1 < max_depth:
                    node["children"] = self._build_tree(child, depth + 1, max_depth, budget)
                    node["lazy"] = False
                else:
                    node["children"] = []
                    node["lazy"] = True
            nodes.append(node)
        return nodes

    def read(self, rel_path: str) -> dict:
        p = self._resolve(rel_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(rel_path)
        if p.suffix.lower() in BINARY_EXTENSIONS:
            return {"path": rel_path, "is_binary": True, "content": None,
                    "size": p.stat().st_size}
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            raise ValueError(f"File too large to read inline ({size} bytes)")
        return {"path": rel_path, "is_binary": False,
                "content": p.read_text(encoding="utf-8", errors="replace"),
                "size": size}

    def write(self, rel_path: str, content: str) -> dict:
        # security-audit P3g: read() has always enforced MAX_READ_BYTES, but
        # write() had no equivalent cap — a caller (or a compromised/buggy
        # Brain tool call) could write an arbitrarily large string to disk
        # in one request, with no size or content check at all. Encode
        # first so the limit is measured in actual bytes-on-disk (UTF-8),
        # not Python string length, which undercounts multi-byte chars.
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_READ_BYTES:
            raise ValueError(f"Content too large to write inline ({len(encoded)} bytes, "
                              f"max {MAX_READ_BYTES})")
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(encoded)
        return {"path": rel_path, "size": p.stat().st_size,
                "written_at": datetime.now(timezone.utc).isoformat()}

    def write_bytes(self, rel_path: str, data: bytes) -> dict:
        if len(data) > MAX_READ_BYTES:
            raise ValueError(f"Content too large ({len(data)} bytes)")
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": rel_path, "size": p.stat().st_size,
                "written_at": datetime.now(timezone.utc).isoformat()}

    def read_bytes(self, rel_path: str) -> bytes:
        p = self._resolve(rel_path)
        if not p.is_file():
            raise FileNotFoundError(rel_path)
        data = p.read_bytes()
        if len(data) > MAX_READ_BYTES:
            data = data[:MAX_READ_BYTES]
        return data

    def create(self, rel_path: str, is_dir: bool = False) -> dict:
        p = self._resolve(rel_path)
        if is_dir:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch(exist_ok=False)
        return {"path": rel_path, "type": "dir" if is_dir else "file"}

    def rename(self, rel_path: str, new_rel_path: str) -> dict:
        src, dst = self._resolve(rel_path), self._resolve(new_rel_path)
        if not src.exists():
            raise FileNotFoundError(rel_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return {"from": rel_path, "to": new_rel_path}

    def delete(self, rel_path: str) -> dict:
        p = self._resolve(rel_path)
        if not p.exists():
            raise FileNotFoundError(rel_path)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"deleted": True, "path": rel_path}
