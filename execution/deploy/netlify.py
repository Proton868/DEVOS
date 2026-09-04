from __future__ import annotations
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
        return DeploymentResult(
            provider=self.name,
            status=DeploymentStatus.FAILED,
            error="NOT_IMPLEMENTED",
            evidence={"note": "Adapter registered; live deploy requires Phase 6 credentials path"},
        )

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        return await self.deploy(project_path="", meta={}, credentials=credentials)
