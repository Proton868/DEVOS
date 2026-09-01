"""
Execution Layer — Version Control (git).
Shells out to the system `git` binary rather than adding GitPython as a
dependency, to stay consistent with requirements-lite.txt / Acer Aspire One
resource targets. Does not decide what to commit or when — that's the
Brain/user's call; this just executes faithfully and reports structured
results.
"""
import asyncio
import logging
from pathlib import Path

from execution.files import PROJECTS_DIR

logger = logging.getLogger("devos.vcs")

GIT_TIMEOUT_S = 30


class GitService:
    def __init__(self, user_id: str, project_id: str):
        self.root = (PROJECTS_DIR / user_id / project_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def _run(self, *args: str) -> dict:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "stdout": "", "stderr": "git command timed out", "exit_code": -1}
        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode(errors="replace").strip(),
            "stderr": stderr.decode(errors="replace").strip(),
            "exit_code": proc.returncode,
        }

    async def is_repo(self) -> bool:
        return (self.root / ".git").exists()

    async def init(self) -> dict:
        return await self._run("init")

    async def status(self) -> dict:
        if not await self.is_repo():
            return {"success": True, "is_repo": False, "branch": None, "files": []}
        branch = await self._run("rev-parse", "--abbrev-ref", "HEAD")
        porcelain = await self._run("status", "--porcelain=v1")
        files = []
        for line in porcelain["stdout"].splitlines():
            if not line.strip():
                continue
            code, path = line[:2], line[3:]
            files.append({"path": path, "status": code.strip()})
        return {"success": True, "is_repo": True,
                "branch": branch["stdout"] if branch["success"] else None,
                "files": files}

    async def stage(self, paths: list[str] | None = None) -> dict:
        return await self._run("add", *(paths or ["."]))

    async def unstage(self, paths: list[str] | None = None) -> dict:
        return await self._run("reset", "HEAD", "--", *(paths or ["."]))

    async def commit(self, message: str, author: str | None = None) -> dict:
        args = ["commit", "-m", message]
        if author:
            args += ["--author", author]
        return await self._run(*args)

    async def push(self, remote: str = "origin", branch: str | None = None) -> dict:
        args = ["push", remote]
        if branch:
            args.append(branch)
        return await self._run(*args)

    async def pull(self, remote: str = "origin", branch: str | None = None) -> dict:
        args = ["pull", remote]
        if branch:
            args.append(branch)
        return await self._run(*args)

    async def checkout(self, branch: str, create: bool = False) -> dict:
        args = ["checkout", "-b", branch] if create else ["checkout", branch]
        return await self._run(*args)

    async def log(self, limit: int = 20) -> dict:
        if not await self.is_repo():
            return {"success": True, "commits": []}
        r = await self._run("log", f"-{limit}", "--pretty=format:%H|%an|%ad|%s", "--date=iso")
        commits = []
        for line in r["stdout"].splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "author": parts[1],
                                 "date": parts[2], "message": parts[3]})
        return {"success": True, "commits": commits}

    async def diff(self, path: str | None = None, staged: bool = False) -> dict:
        """Unified diff for one file (or everything, if path is None).
        staged=True shows what's staged (--cached); False shows working-tree
        changes not yet staged — matches the two states a git panel needs to
        render separately."""
        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            args += ["--", path]
        r = await self._run(*args)
        return {"success": r["success"], "diff": r["stdout"]}

    async def discard(self, path: str) -> dict:
        """Discard working-tree changes to a tracked file, reverting it to
        HEAD. Irreversible for uncommitted work, so this is registered as a
        non-reversible tool contract (see governance/tool_contracts.py) —
        an autonomous agent calling this goes through HITL; direct human
        IDE use does not, same trust-model reasoning as terminal.py."""
        return await self._run("checkout", "--", path)
