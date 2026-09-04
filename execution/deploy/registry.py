from __future__ import annotations
from typing import Dict, Type
from .base import DeploymentAdapter

_REGISTRY: Dict[str, Type[DeploymentAdapter]] = {}


def register(cls: Type[DeploymentAdapter]):
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(name: str) -> DeploymentAdapter:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown deployment provider: {name}")
    return _REGISTRY[name]()


def list_adapters() -> list[str]:
    return sorted(_REGISTRY.keys())
