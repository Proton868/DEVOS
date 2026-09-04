from __future__ import annotations
import io
import zipfile
from pathlib import Path
import httpx
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import register


@register
class NetlifyAdapter(DeploymentAdapter):
    name = "netlify"

    async def deploy(self, *, project_path: str, meta: dict, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("NETLIFY_TOKEN") or (credentials or {}).get("token")
        site_id = (credentials or {}).get("NETLIFY_SITE_ID") or meta.get("site_id")
        if not token:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED,
                                    error="DEPLOYMENT_AUTH_REQUIRED", evidence={"reason": "missing_netlify_token"})
        if not site_id:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED,
                                    error="DEPLOYMENT_FAILED", evidence={"reason": "NETLIFY_SITE_ID required"})
        root = meta.get("workspace_root")
        if not root or not Path(root).is_dir():
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED,
                                    error="DEPLOYMENT_FAILED", evidence={"reason": "workspace_root required"})
        buf = io.BytesIO()
        skip = {".git", "node_modules", ".next", ".venv"}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in Path(root).rglob("*"):
                if not p.is_file():
                    continue
                if any(part in skip for part in p.parts):
                    continue
                rel = p.relative_to(root).as_posix()
                if rel.startswith(".env"):
                    continue
                zf.writestr(rel, p.read_bytes())
        blob = buf.getvalue()
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/zip",
                    },
                    content=blob,
                )
            if r.status_code >= 400:
                return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED,
                                        error="DEPLOYMENT_FAILED",
                                        evidence={"http": r.status_code, "body": r.text[:400]})
            data = r.json()
            url = data.get("ssl_url") or data.get("url")
            state = data.get("state")
            st = DeploymentStatus.DEPLOYED if state == "ready" else DeploymentStatus.BUILDING
            return DeploymentResult(
                provider=self.name, status=st, deployment_id=data.get("id"), url=url,
                evidence={"state": state},
            )
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("NETLIFY_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_AUTH_REQUIRED")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"https://api.netlify.com/api/v1/deploys/{deployment_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code >= 400:
                return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_FAILED",
                                        evidence={"http": r.status_code})
            data = r.json()
            state = data.get("state")
            url = data.get("ssl_url") or data.get("url")
            st = DeploymentStatus.DEPLOYED if state == "ready" else DeploymentStatus.BUILDING
            if state in ("error", "failed"):
                st = DeploymentStatus.FAILED
            verified = False
            if st == DeploymentStatus.DEPLOYED and url:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        vr = await client.get(url)
                    verified = vr.status_code < 500
                except Exception:
                    pass
            return DeploymentResult(provider=self.name, status=st, deployment_id=deployment_id, url=url,
                                    evidence={"state": state, "http_verified": verified})
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))
