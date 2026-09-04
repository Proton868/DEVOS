"""
Application Runtime — runs user applications for preview (NOT Agent Runtime).

Security contract:
  - no DevOS auth tokens in env
  - no provider credentials
  - workspace-scoped cwd only
  - resource limits via isolation backends when available
  - never binds public 0.0.0.0 without explicit policy
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AppRuntimeState(str, Enum):
    UNKNOWN = "UNKNOWN"
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    BUILT = "BUILT"
    STARTING = "STARTING"
    READY = "READY"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class AppRuntimeSpec:
    user_id: str
    project_id: str
    kind: str
    build_command: Optional[str] = None
    start_command: Optional[str] = None
    port: Optional[int] = None


@dataclass
class AppRuntimeStatus:
    state: AppRuntimeState
    detail: str = ""
    port: Optional[int] = None
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "detail": self.detail,
            "port": self.port,
            "evidence": self.evidence,
        }


# Env keys that must never be forwarded into application processes
_BLOCKED_ENV_PREFIXES = (
    "OPENROUTER_", "OPENAI_", "GITHUB_", "VERCEL_", "NETLIFY_", "CLOUDFLARE_",
    "JWT_", "SUPABASE_", "DATABASE_", "REDIS_", "DEVOS_", "AWS_", "GOOGLE_",
)


def filter_env(env: Optional[dict] = None) -> dict:
    base = dict(env or {})
    out = {}
    for k, v in base.items():
        ku = k.upper()
        if any(ku.startswith(p) or ku == p.rstrip("_") for p in _BLOCKED_ENV_PREFIXES):
            continue
        if "SECRET" in ku or "TOKEN" in ku or "PASSWORD" in ku or "API_KEY" in ku:
            continue
        out[k] = v
    # minimal clean env
    out.setdefault("NODE_ENV", "development")
    out.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    return out


class ApplicationRuntime:
    """Placeholder runtime manager — Phase 3 implements process/container spawn."""

    def __init__(self, spec: AppRuntimeSpec):
        self.spec = spec
        self.status = AppRuntimeStatus(state=AppRuntimeState.PENDING)

    async def build(self) -> AppRuntimeStatus:
        self.status = AppRuntimeStatus(
            state=AppRuntimeState.UNSUPPORTED,
            detail="Application runtime process spawn is Phase 3; detection is available",
            evidence={"kind": self.spec.kind},
        )
        return self.status

    async def start(self) -> AppRuntimeStatus:
        return await self.build()

    async def stop(self) -> AppRuntimeStatus:
        self.status = AppRuntimeStatus(state=AppRuntimeState.STOPPED, detail="stopped")
        return self.status
