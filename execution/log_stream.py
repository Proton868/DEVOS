"""Bounded log ring + SSE helpers for Application Runtime."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import AsyncIterator, Deque, Dict, Tuple

from execution.durable_store import append_log, read_logs

# in-process subscribers: runtime_id -> list of queues
_SUBS: Dict[str, list] = defaultdict(list)
_RING: Dict[str, Deque[Tuple[float, str, str]]] = defaultdict(lambda: deque(maxlen=2000))


def publish_log(runtime_id: str, stream: str, line: str) -> None:
    ts = time.time()
    # redact crude secrets
    lower = line.lower()
    if any(k in lower for k in ("api_key", "token=", "password=", "bearer ", "secret=")):
        line = "[REDACTED]"
    _RING[runtime_id].append((ts, stream, line))
    try:
        append_log(runtime_id, stream, line)
    except Exception:
        pass
    for q in list(_SUBS.get(runtime_id, [])):
        try:
            q.put_nowait({"ts": ts, "stream": stream, "line": line})
        except Exception:
            pass


def recent(runtime_id: str, limit: int = 200) -> list[dict]:
    rows = read_logs(runtime_id, after_id=0, limit=limit)
    if rows:
        return [{"id": r["id"], "ts": r["ts"], "stream": r["stream"], "line": r["line"]} for r in rows]
    return [{"ts": t, "stream": s, "line": l} for t, s, l in list(_RING[runtime_id])[-limit:]]


async def subscribe(runtime_id: str) -> AsyncIterator[dict]:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _SUBS[runtime_id].append(q)
    try:
        for item in recent(runtime_id, 100):
            yield item
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15.0)
                yield item
            except asyncio.TimeoutError:
                yield {"ts": time.time(), "stream": "system", "line": "", "keepalive": True}
    finally:
        if q in _SUBS[runtime_id]:
            _SUBS[runtime_id].remove(q)
