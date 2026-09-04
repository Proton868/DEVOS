"""GitHub REST client — real when GITHUB_TOKEN set; mock for tests only via DEVOS_GITHUB_MOCK=1."""
from __future__ import annotations
import os
import httpx
from typing import Any, Optional


class GitHubError(Exception):
    def __init__(self, code: str, message: str, http: int = 0):
        self.code = code
        self.http = http
        super().__init__(message)


def _token(credentials: Optional[dict] = None) -> Optional[str]:
    if credentials and credentials.get("token"):
        return credentials["token"]
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


class GitHubClient:
    def __init__(self, credentials: Optional[dict] = None):
        self.token = _token(credentials)
        self.mock = os.environ.get("DEVOS_GITHUB_MOCK") == "1"

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def whoami(self) -> dict:
        if self.mock:
            return {"login": "mock-user", "id": 1}
        if not self.token:
            raise GitHubError("GITHUB_NOT_CONNECTED", "missing GITHUB_TOKEN")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://api.github.com/user", headers=self._headers())
        if r.status_code in (401, 403):
            raise GitHubError("GITHUB_AUTH_FAILED", r.text[:200], r.status_code)
        if r.status_code >= 400:
            raise GitHubError("GITHUB_ERROR", r.text[:200], r.status_code)
        return r.json()

    async def create_repo(self, name: str, *, private: bool = True, description: str = "") -> dict:
        if self.mock:
            return {"full_name": f"mock-user/{name}", "html_url": f"https://github.com/mock-user/{name}", "clone_url": f"https://github.com/mock-user/{name}.git"}
        if not self.token:
            raise GitHubError("GITHUB_NOT_CONNECTED", "missing GITHUB_TOKEN")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.github.com/user/repos",
                headers=self._headers(),
                json={"name": name, "private": private, "description": description, "auto_init": False},
            )
        if r.status_code >= 400:
            raise GitHubError("GITHUB_ERROR", r.text[:300], r.status_code)
        return r.json()

    async def create_pr(self, owner: str, repo: str, *, title: str, head: str, base: str = "main", body: str = "") -> dict:
        if self.mock:
            return {"number": 1, "html_url": f"https://github.com/{owner}/{repo}/pull/1", "title": title}
        if not self.token:
            raise GitHubError("GITHUB_NOT_CONNECTED", "missing GITHUB_TOKEN")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers=self._headers(),
                json={"title": title, "head": head, "base": base, "body": body},
            )
        if r.status_code >= 400:
            raise GitHubError("GITHUB_ERROR", r.text[:300], r.status_code)
        return r.json()

    async def list_branches(self, owner: str, repo: str) -> list:
        if self.mock:
            return [{"name": "main"}]
        if not self.token:
            raise GitHubError("GITHUB_NOT_CONNECTED", "missing GITHUB_TOKEN")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"https://api.github.com/repos/{owner}/{repo}/branches", headers=self._headers())
        if r.status_code >= 400:
            raise GitHubError("GITHUB_ERROR", r.text[:200], r.status_code)
        return r.json()
