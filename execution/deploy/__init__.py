"""Provider-neutral deployment adapters. Credentials stay server-side; UCIP gates EXTERNAL_SIDE_EFFECT."""
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import get_adapter, list_adapters

__all__ = [
    "DeploymentAdapter",
    "DeploymentResult",
    "DeploymentStatus",
    "get_adapter",
    "list_adapters",
]
