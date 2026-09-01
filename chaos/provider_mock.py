"""Test provider that can apply a side effect then drop the response."""
from __future__ import annotations

from typing import Optional


class DropResponseProvider:
    """Simulates: receive request → mutate → never return HTTP body."""

    def __init__(self):
        self.effects: dict[str, dict] = {}
        self.calls = 0

    async def mutate(self, *, idempotency_key: str, payload: dict, drop_response: bool = True):
        self.calls += 1
        # Side effect always applied if we get this far
        self.effects[idempotency_key] = {
            "id": f"ext-{idempotency_key[:8]}",
            "status": "success",
            "payload": payload,
        }
        if drop_response:
            raise TimeoutError("provider accepted request then dropped response")
        return {"status": "success", "id": self.effects[idempotency_key]["id"]}

    async def reconcile(self, idempotency_key: str) -> Optional[dict]:
        return self.effects.get(idempotency_key)
