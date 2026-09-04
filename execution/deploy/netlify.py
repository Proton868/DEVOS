from __future__ import annotations
import httpx
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import register


@register
class NetlifyAdapter(DeploymentAdapter):
    name = "netlify"

    async def deploy(self, *, project_path: str, meta: dict, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("NETLIFY_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(
                provider=self.name,
                status=DeploymentStatus.FAILED,
                error="DEPLOYMENT_AUTH_REQUIRED",
                evidence={"reason": "missing_netlify_token"},
            )
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    "https://api.netlify.com/api/v1/user",
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
                    evidence={"http": r.status_code},
                )
            return DeploymentResult(
                provider=self.name,
                status=DeploymentStatus.AUTHORIZED,
                evidence={"user": r.json() if "json" in r.headers.get("content-type", "") else {},
                          "note": "Authenticated; site-linked deploy requires NETLIFY_SITE_ID"},
            )
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("NETLIFY_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error="DEPLOYMENT_AUTH_REQUIRED")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
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
            return DeploymentResult(provider=self.name, status=st, deployment_id=deployment_id, url=url,
                                    evidence={"state": state})
        except httpx.HTTPError as e:
            return DeploymentResult(provider=self.name, status=DeploymentStatus.FAILED, error=str(e))
