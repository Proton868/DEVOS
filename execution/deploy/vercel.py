"""Vercel adapter — real API when VERCEL_TOKEN present; otherwise fail-closed."""
from __future__ import annotations
import httpx
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import register


@register
class VercelAdapter(DeploymentAdapter):
    name = "vercel"

    async def deploy(self, *, project_path: str, meta: dict, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("VERCEL_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(
                provider=self.name,
                status=DeploymentStatus.FAILED,
                error="DEPLOYMENT_AUTH_REQUIRED",
                evidence={"reason": "missing_vercel_token"},
            )
        # Lightweight connectivity probe — full file upload deploy is project-specific
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    "https://api.vercel.com/v2/user",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code in (401, 403):
                return DeploymentResult(
                    provider=self.name,
                    status=DeploymentStatus.FAILED,
                    error="DEPLOYMENT_AUTH_FAILED",
                    evidence={"http": r.status_code},
                )
            if r.status_code >= 400:
                return DeploymentResult(
                    provider=self.name,
                    status=DeploymentStatus.FAILED,
                    error="DEPLOYMENT_FAILED",
                    evidence={"http": r.status_code, "body": r.text[:300]},
                )
            # Auth OK — deployment upload requires project linkage; report authorized not deployed
            return DeploymentResult(
                provider=self.name,
                status=DeploymentStatus.AUTHORIZED,
                evidence={"user": r.json() if r.headers.get("content-type", "").startswith("application/json") else {},
                          "note": "Authenticated; project-linked deploy requires VERCEL_PROJECT_ID + file upload API"},
            )
        except httpx.HTTPError as e:
            return DeploymentResult(
                provider=self.name,
                status=DeploymentStatus.FAILED,
                error="DEPLOYMENT_FAILED",
                evidence={"error": str(e)},
            )

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("VERCEL_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_AUTH_REQUIRED")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"https://api.vercel.com/v13/deployments/{deployment_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code >= 400:
                return DeploymentResult(
                    provider=self.name,
                    status=DeploymentStatus.FAILED,
                    error="DEPLOYMENT_FAILED",
                    evidence={"http": r.status_code},
                )
            data = r.json()
            ready = data.get("readyState") or data.get("state")
            url = data.get("url")
            if url and not str(url).startswith("http"):
                url = f"https://{url}"
            st = DeploymentStatus.DEPLOYED if ready in ("READY", "ready") else DeploymentStatus.BUILDING
            return DeploymentResult(
                provider=self.name,
                status=st,
                deployment_id=deployment_id,
                url=url,
                evidence={"readyState": ready},
            )
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))
