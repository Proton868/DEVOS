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
        """Resolve a relative path inside the project root; refuse escapes."""
        rel_path = (rel_path or "").lstrip("/")
        candidate = (self.root / rel_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PathViolation(f"Path escapes project root: {rel_path}")
        return candidate

    def tree(self) -> list[dict]:
        """Flat list of every file/dir under the project root, relative paths.
        Excludes .git internals — version control state belongs in the git
        panel (execution/vcs.py), not the file tree."""
        items = []
        for p in sorted(self.root.rglob("*")):
            rel = str(p.relative_to(self.root))
            if rel == ".git" or rel.startswith(".git/"):
                continue
            items.append({
                "path": rel,
                "type": "dir" if p.is_dir() else "file",
                "size": p.stat().st_size if p.is_file() else None,
                "is_binary": p.suffix.lower() in BINARY_EXTENSIONS,
            })
        return items

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
