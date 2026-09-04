"""Cloudflare Tunnel adapter — routing/exposure, not static deploy."""
from __future__ import annotations
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import register


@register
class CloudflareTunnelAdapter(DeploymentAdapter):
    name = "cloudflare_tunnel"

    async def deploy(self, *, project_path: str, meta: dict, credentials: dict) -> DeploymentResult:
        token = (credentials or {}).get("CLOUDFLARE_TOKEN") or (credentials or {}).get("token")
        if not token:
            return DeploymentResult(
                provider=self.name,
                status=DeploymentStatus.FAILED,
                error="DEPLOYMENT_AUTH_REQUIRED",
                evidence={"reason": "missing_cloudflare_token"},
            )
        return DeploymentResult(
            provider=self.name,
            status=DeploymentStatus.FAILED,
            error="NOT_IMPLEMENTED",
            evidence={"note": "Tunnel is exposure of a running application runtime, not a static host"},
        )

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        return await self.deploy(project_path="", meta={}, credentials=credentials)
