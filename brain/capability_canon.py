"""
Canonical capability names for orchestration / specialty policy.

Aliases are compatibility syntax only — they must NOT expand authority.
"""
from __future__ import annotations

# Canonical form used in logs, policy decisions, and persisted effective caps
CANONICAL = {
    "filesystem.read",
    "filesystem.write",
    "filesystem.delete",
    "shell.execute",
    "web.search",
    "workflow.write",
    "credentials.read",
    "deployment.production",
    "production.delete",
    "external.publish",
    "db.drop",
    "memory.read",
    "memory.write",
    "api.call",
    "package.install",
    "vcs.write",
}

# alias / ucip form → canonical
_ALIASES: dict[str, str] = {
    "fs.read": "filesystem.read",
    "fs.write": "filesystem.write",
    "fs.delete": "filesystem.delete",
    "shell.exec": "shell.execute",
    "shell.execute": "shell.execute",
    "web.search": "web.search",
    "workflow.write": "workflow.write",
    "credentials.read": "credentials.read",
    "production.delete": "production.delete",
    "deployment.production": "deployment.production",
    "external.publish": "external.publish",
    "db.drop": "db.drop",
    "ucip:filesystem.read": "filesystem.read",
    "ucip:filesystem.write": "filesystem.write",
    "ucip:filesystem.delete": "filesystem.delete",
    "ucip:execution.bash": "shell.execute",
    "ucip:execution.python": "shell.execute",
    "ucip:execution.node": "shell.execute",
    "ucip:search.web": "web.search",
    "ucip:secret.read": "credentials.read",
    "ucip:memory.read": "memory.read",
    "ucip:memory.write": "memory.write",
    "ucip:api.call": "api.call",
    "ucip:package.install": "package.install",
    "ucip:vcs.write": "vcs.write",
    "ucip:network.outbound": "external.publish",
    "ucip:filesystem.format": "db.drop",
}

# canonical → preferred UCIP form for gateway checks
_TO_UCIP: dict[str, str] = {
    "filesystem.read": "ucip:filesystem.read",
    "filesystem.write": "ucip:filesystem.write",
    "filesystem.delete": "ucip:filesystem.delete",
    "shell.execute": "ucip:execution.bash",
    "web.search": "ucip:search.web",
    "credentials.read": "ucip:secret.read",
    "memory.read": "ucip:memory.read",
    "memory.write": "ucip:memory.write",
    "api.call": "ucip:api.call",
    "package.install": "ucip:package.install",
    "vcs.write": "ucip:vcs.write",
    "external.publish": "ucip:network.outbound",
    "deployment.production": "ucip:network.outbound",
    "production.delete": "ucip:filesystem.delete",
    "db.drop": "ucip:filesystem.format",
    "workflow.write": "ucip:filesystem.write",
}


def canonicalize(cap: str) -> str:
    c = (cap or "").strip()
    if not c:
        return c
    if c in _ALIASES:
        return _ALIASES[c]
    if c.startswith("ucip:") and c in _ALIASES:
        return _ALIASES[c]
    # already canonical or unknown pass-through
    return _ALIASES.get(c, c)


def canonicalize_set(caps) -> set[str]:
    return {canonicalize(c) for c in (caps or []) if c}


def to_ucip(cap: str) -> str:
    can = canonicalize(cap)
    return _TO_UCIP.get(can, can if can.startswith("ucip:") else f"ucip:{can}")


def aliases_are_same_authority(a: str, b: str) -> bool:
    """True iff a and b normalize to the same canonical capability (no expansion)."""
    return canonicalize(a) == canonicalize(b)
