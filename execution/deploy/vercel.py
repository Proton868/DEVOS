"""Vercel adapter — LIVE requires credentials; fails closed without them."""
from __future__ import annotations
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
        # Live HTTP deploy not auto-run without explicit operator path in this phase
        return DeploymentResult(
            provider=self.name,
            status=DeploymentStatus.FAILED,
            error="NOT_IMPLEMENTED",
            evidence={"note": "Adapter registered; live deploy requires Phase 6 credentials path"},
        )

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        return await self.deploy(project_path="", meta={}, credentials=credentials)
