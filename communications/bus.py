"""
Communications — the nervous system.

Every other organ (Governance, Runtime, Cognitive System, Workers) publishes
events here instead of calling each other's callback lists directly. This is
the first concrete piece of the Communications organ from the v2 architecture
(Agency-OS-Master-Plan.md §1) — an in-process async pub/sub bus, with a
bounded per-topic replay buffer so a client that connects slightly late (or
briefly drops and reconnects) doesn't miss what just happened.

Scope note: this is intentionally in-process only for now (single FastAPI
process, matching the Acer Aspire One Micro/Standard profiles). Distributed
execution (multiple DevOS processes/machines) is real Communications-organ
scope per the master plan, but is out of scope for this pass — it would need
a real broker (Redis pub/sub or NATS) behind the same publish()/subscribe()
interface. The interface below is written so that swap is possible later
without touching any caller.
"""
import asyncio
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import AsyncIterator

logger = logging.getLogger("devos.comms")

REPLAY_BUFFER_SIZE = 50  # events retained per topic for late/reconnecting subscribers


class EventBus:
    """Singleton in-process pub/sub bus. Topics are free-form strings —
    convention used elsewhere in the codebase: 'user:{user_id}' for
    per-user event streams, 'system' for organ-wide broadcasts."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
                    cls._instance._replay: dict[str, deque] = defaultdict(lambda: deque(maxlen=REPLAY_BUFFER_SIZE))
        return cls._instance

    async def publish(self, topic: str, event_type: str, data: dict) -> dict:
        """Publish an event to a topic. Every current subscriber's queue gets
        it immediately; it's also kept in the replay buffer for late joiners."""
        event = {
            "id": str(uuid.uuid4()),
            "topic": topic,
            "type": event_type,
            "data": data,
            "ts": time.time(),
        }
        self._replay[topic].append(event)
        dead = []
        for q in self._subscribers.get(topic, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)  # slow consumer — drop rather than block publishers
        for q in dead:
            self._subscribers[topic].remove(q)
        logger.debug(f"[comms] published {event_type} on {topic} ({len(self._subscribers.get(topic, []))} subscribers)")
        return event

    def replay(self, topic: str) -> list[dict]:
        """Events already published on this topic, oldest first, for a
        subscriber that just connected."""
        return list(self._replay.get(topic, []))

    async def subscribe(self, topic: str) -> AsyncIterator[dict]:
        """Async-iterate events on a topic as they arrive. Replays buffered
        history first so a client that connects mid-stream isn't blind to
        what already happened."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[topic].append(q)
        try:
            for event in self.replay(topic):
                yield event
            while True:
                event = await q.get()
                yield event
        finally:
            if q in self._subscribers.get(topic, []):
                self._subscribers[topic].remove(q)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, []))
