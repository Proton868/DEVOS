"""
World-scoped cache key helpers.

Use only for world-sensitive state. Global catalogs/configs need not be world-qualified.
"""
from __future__ import annotations

from typing import Any, Optional

from governance.world_context import WorldBoundaryError, WorldContext, require_world_context


def world_cache_key(world: WorldContext, *parts: Any) -> str:
    """Build a cache key that cannot collide across worlds."""
    world = require_world_context(world)
    safe = [str(world.world_id)]
    for p in parts:
        s = str(p).replace(":", "_")
        if ".." in s or s.startswith("/"):
            raise WorldBoundaryError("INVALID_KEY", f"unsafe cache part: {s!r}")
        safe.append(s)
    return "world:" + ":".join(safe)


def parse_world_from_cache_key(key: str) -> Optional[str]:
    if not key or not str(key).startswith("world:"):
        return None
    parts = str(key).split(":")
    return parts[1] if len(parts) > 1 else None


def assert_cache_key_world(world: WorldContext, key: str) -> None:
    world = require_world_context(world)
    wid = parse_world_from_cache_key(key)
    if not wid:
        raise WorldBoundaryError("WORLD_REQUIRED", "cache key not world-qualified")
    world.assert_same_world(wid, resource="cache_key")


class WorldScopedRegistry:
    """Simple process-local registry that refuses cross-world access."""

    def __init__(self):
        self._data: dict[str, dict[str, Any]] = {}

    def put(self, world: WorldContext, key: str, value: Any) -> None:
        world = require_world_context(world)
        bucket = self._data.setdefault(world.world_id, {})
        bucket[key] = value

    def get(self, world: WorldContext, key: str) -> Any:
        world = require_world_context(world)
        bucket = self._data.get(world.world_id) or {}
        if key not in bucket:
            raise WorldBoundaryError("NOT_FOUND", "cache miss or cross-world")
        return bucket[key]

    def delete(self, world: WorldContext, key: str) -> None:
        world = require_world_context(world)
        bucket = self._data.get(world.world_id)
        if bucket and key in bucket:
            del bucket[key]
