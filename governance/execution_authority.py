"""Execution authority invariant.

NO AI-INITIATED CODE/EFFECT EXECUTION unless:
  Identity + UCI capability + isolation + ExecutionJob (if durable) + Evidence

Exceptions must be explicitly typed PathClass, never accidental.
ExecutionLayer is a primitive only — not a security authority.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any

from governance.execution_pipeline import PathClass

logger = logging.getLogger("devos.execution_authority")

# In-process audit of explicit exceptions (also written to evidence when possible)
_EXCEPTION_LOG: list[dict] = []


@dataclass
class ExecutionAuthority:
    path_class: PathClass
    actor_id: str
    tenant_id: Optional[str] = None
    capability: Optional[str] = None
    job_id: Optional[str] = None
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path_class": self.path_class.value if isinstance(self.path_class, PathClass) else str(self.path_class),
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "capability": self.capability,
            "job_id": self.job_id,
            "reason": self.reason,
            "metadata": self.metadata,
            "at": datetime.now(timezone.utc).isoformat(),
        }


def require_authority(
    *,
    path_class: PathClass,
    actor_id: str,
    tenant_id: Optional[str] = None,
    capability: Optional[str] = None,
    job_id: Optional[str] = None,
    reason: str = "",
    metadata: Optional[dict] = None,
) -> ExecutionAuthority:
    """Stamp and log authority for an execution. Call BEFORE any primitive."""
    auth = ExecutionAuthority(
        path_class=path_class,
        actor_id=actor_id,
        tenant_id=tenant_id,
        capability=capability,
        job_id=job_id,
        reason=reason,
        metadata=metadata or {},
    )
    entry = auth.to_dict()
    _EXCEPTION_LOG.append(entry)
    if len(_EXCEPTION_LOG) > 500:
        del _EXCEPTION_LOG[:-500]
    if path_class in (PathClass.HUMAN_ONLY, PathClass.NON_DURABLE, PathClass.READ_ONLY):
        logger.info("explicit path exception: %s", entry)
    else:
        logger.debug("execution authority: %s", entry)
    return auth


def recent_authority_log(limit: int = 50) -> list[dict]:
    return list(_EXCEPTION_LOG[-limit:])


# Capability names for peripheral surfaces
CAP_PACKAGE_INSTALL = "ucip:package.install"
CAP_SEARCH_WEB = "ucip:search.web"
CAP_EXECUTION_PYTHON = "ucip:execution.python"
CAP_AUTORESEARCH = "ucip:execution.python"  # research measures code; no network
