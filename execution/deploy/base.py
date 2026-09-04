from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DeploymentStatus(str, Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    UPLOADING = "UPLOADING"
    BUILDING = "BUILDING"
    VERIFYING = "VERIFYING"
    DEPLOYED = "DEPLOYED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class DeploymentResult:
    provider: str
    status: DeploymentStatus
    deployment_id: Optional[str] = None
    url: Optional[str] = None
    error: Optional[str] = None
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "deployment_id": self.deployment_id,
            "url": self.url,
            "error": self.error,
            "evidence": self.evidence,
        }


class DeploymentAdapter:
    """Base adapter — subclasses must not run without real credentials."""

    name: str = "base"

    async def deploy(self, *, project_path: str, meta: dict, credentials: dict) -> DeploymentResult:
        raise NotImplementedError

    async def status(self, deployment_id: str, credentials: dict) -> DeploymentResult:
        raise NotImplementedError
