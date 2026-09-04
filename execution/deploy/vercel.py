"""Vercel deployment — real file deploy when token present."""
from __future__ import annotations
import hashlib
import httpx
from pathlib import Path
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import register


@register
class VercelAdapter(DeploymentAdapter):
    name = "vercel"

    async def deploy(self, *, project_path: str, meta: dict, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("VERCEL_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(
                provider=self.name, status=DeploymentStatus.FAILED,
                error="DEPLOYMENT_AUTH_REQUIRED", evidence={"reason": "missing_vercel_token"},
            )
        root = meta.get("workspace_root")
        if not root or not Path(root).is_dir():
            return DeploymentResult(
                provider=self.name, status=DeploymentStatus.FAILED,
                error="DEPLOYMENT_FAILED",
                evidence={"reason": "workspace_root required for file deploy"},
            )
        files = []
        skip = {".git", "node_modules", ".next", ".venv", "__pycache__"}
        for p in Path(root).rglob("*"):
            if not p.is_file():
                continue
            if any(part in skip for part in p.parts):
                continue
            rel = p.relative_to(root).as_posix()
            if rel.startswith(".env"):
                continue
            data = p.read_bytes()
            if len(data) > 5 * 1024 * 1024:
                continue
            sha = hashlib.sha1(data).hexdigest()
            files.append({"file": rel, "sha": sha, "size": len(data), "data": data})
        if not files:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_FAILED",
                                    evidence={"reason": "no files"})
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                # upload files
                for f in files:
                    put = await client.put(
                        f"https://api.vercel.com/v2/files/{f['sha']}",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Length": str(f["size"]),
                            "x-vercel-digest": f["sha"],
                        },
                        content=f["data"],
                    )
                    if put.status_code not in (200, 201, 409):
                        return DeploymentResult(
                            provider=self.name, status=DeploymentStatus.FAILED,
                            error="DEPLOYMENT_FAILED",
                            evidence={"phase": "upload", "file": f["file"], "http": put.status_code, "body": put.text[:300]},
                        )
                payload = {
                    "name": meta.get("project_name") or Path(root).name,
                    "files": [{"file": f["file"], "sha": f["sha"], "size": f["size"]} for f in files],
                    "projectSettings": {"framework": meta.get("framework") or "nextjs"},
                }
                if meta.get("project_id"):
                    payload["project"] = meta["project_id"]
                r = await client.post(
                    "https://api.vercel.com/v13/deployments",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code >= 400:
                    return DeploymentResult(
                        provider=self.name, status=DeploymentStatus.FAILED,
                        error="DEPLOYMENT_FAILED",
                        evidence={"phase": "create", "http": r.status_code, "body": r.text[:500]},
                    )
                data = r.json()
                url = data.get("url")
                if url and not str(url).startswith("http"):
                    url = f"https://{url}"
                return DeploymentResult(
                    provider=self.name,
                    status=DeploymentStatus.BUILDING if data.get("readyState") != "READY" else DeploymentStatus.DEPLOYED,
                    deployment_id=data.get("id") or data.get("uid"),
                    url=url,
                    evidence={"readyState": data.get("readyState"), "name": data.get("name")},
                )
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("VERCEL_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_AUTH_REQUIRED")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"https://api.vercel.com/v13/deployments/{deployment_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code >= 400:
                return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_FAILED",
                                        evidence={"http": r.status_code})
            data = r.json()
            ready = data.get("readyState")
            url = data.get("url")
            if url and not str(url).startswith("http"):
                url = f"https://{url}"
            st = DeploymentStatus.DEPLOYED if ready == "READY" else DeploymentStatus.BUILDING
            if ready in ("ERROR", "CANCELED"):
                st = DeploymentStatus.FAILED
            # optional HTTP verification
            verified = False
            if st == DeploymentStatus.DEPLOYED and url:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        vr = await client.get(url)
                    verified = vr.status_code < 500
                except Exception:
                    verified = False
            return DeploymentResult(
                provider=self.name, status=st, deployment_id=deployment_id, url=url,
                evidence={"readyState": ready, "http_verified": verified},
            )
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))
