"""Provider-neutral deployment adapters. Credentials stay server-side; UCIP gates EXTERNAL_SIDE_EFFECT."""
from .base import DeploymentAdapter, DeploymentResult, DeploymentStatus
from .registry import get_adapter, list_adapters, register

# Force adapter registration
from . import vercel as _vercel  # noqa: F401
from . import netlify as _netlify  # noqa: F401
from . import cloudflare_tunnel as _cf  # noqa: F401

__all__ = [
    "DeploymentAdapter",
    "DeploymentResult",
    "DeploymentStatus",
    "get_adapter",
    "list_adapters",
    "register",
]
