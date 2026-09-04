"""Compensation policies for Saga steps. Never invent destructive remote history deletion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

CompensationMode = Literal["NONE", "AUTOMATIC", "CONDITIONAL", "MANUAL"]


@dataclass(frozen=True)
class CompensationPolicy:
    mode: CompensationMode
    action: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {"mode": self.mode, "action": self.action, "reason": self.reason}


# Action → compensation policy (explicit registry)
COMPENSATION_REGISTRY: dict[str, CompensationPolicy] = {
    "inspect": CompensationPolicy("NONE", reason="read-only"),
    "install": CompensationPolicy("NONE", reason="workspace artifacts retained"),
    "build": CompensationPolicy("NONE", reason="build outputs retained"),
    "verify": CompensationPolicy("NONE", reason="read-only"),
    "preview": CompensationPolicy("AUTOMATIC", action="STOP_RUNTIME", reason="stop ephemeral runtime"),
    "start_runtime": CompensationPolicy("AUTOMATIC", action="STOP_RUNTIME"),
    "create_share": CompensationPolicy("AUTOMATIC", action="REVOKE_SHARE"),
    "share": CompensationPolicy("AUTOMATIC", action="REVOKE_SHARE"),
    "create_tunnel": CompensationPolicy("AUTOMATIC", action="STOP_TUNNEL"),
    "tunnel": CompensationPolicy("AUTOMATIC", action="STOP_TUNNEL"),
    "create_preview": CompensationPolicy("AUTOMATIC", action="REVOKE_PREVIEW"),
    "git_commit": CompensationPolicy("MANUAL", reason="preserve local commit history"),
    "github_commit": CompensationPolicy("MANUAL", reason="preserve commit"),
    "github_branch": CompensationPolicy("CONDITIONAL", action="DELETE_BRANCH", reason="only unmerged local branch"),
    "github_push": CompensationPolicy("MANUAL", reason="do not rewrite remote history"),
    "github_pr": CompensationPolicy("CONDITIONAL", action="CLOSE_PR", reason="close only if policy allows"),
    "deploy": CompensationPolicy("MANUAL", reason="preserve provider deployment"),
    "deploy_vercel": CompensationPolicy("MANUAL", reason="preserve Vercel deployment"),
    "deploy_netlify": CompensationPolicy("MANUAL", reason="preserve Netlify deployment"),
    "deploy_verify": CompensationPolicy("NONE", reason="read-only"),
    "publish": CompensationPolicy("MANUAL", reason="require review to unpublish"),
    "publication": CompensationPolicy("MANUAL", reason="require review"),
}


def policy_for(action: str) -> CompensationPolicy:
    key = (action or "").lower().strip()
    return COMPENSATION_REGISTRY.get(key, CompensationPolicy("MANUAL", reason="unknown action — default manual"))
