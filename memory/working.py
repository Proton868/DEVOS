"""
Memory — Working division.

Deliberately NOT part of memory/store.py's persisted `memories` table.
Working memory is active task/session scratch space — mid-execution state
that should vanish when the task ends or the process restarts, not
accumulate forever. Persisting it would be a bug (it would silently turn
scratch data into permanent Long-term/Episodic entries), so this is a
separate, simple, TTL'd in-process dict instead of a division column like
the other six.

Scope note, same as communications/bus.py: in-process only, fits the Acer
Aspire One Micro/Standard profiles. If this ever needs to survive a process
restart or be shared across multiple DevOS processes, that's a genuinely
different requirement (that's what Long-term/Tenant divisions in
memory/store.py are for) — don't be tempted to just persist this dict.
"""
import threading
import time
from typing import Optional


class WorkingMemory:
    """Singleton in-process TTL cache, keyed by (session_id, key)."""

    _instance = None
    _lock = threading.Lock()
    DEFAULT_TTL_S = 3600  # 1 hour — a task's scratch space shouldn't outlive its session by much

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._store: dict[str, tuple[float, object]] = {}
        return cls._instance

    def _full_key(self, session_id: str, key: str) -> str:
        return f"{session_id}:{key}"

    def set(self, session_id: str, key: str, value, ttl_s: Optional[int] = None):
        expires_at = time.time() + (ttl_s if ttl_s is not None else self.DEFAULT_TTL_S)
        self._store[self._full_key(session_id, key)] = (expires_at, value)

    def get(self, session_id: str, key: str, default=None):
        entry = self._store.get(self._full_key(session_id, key))
        if entry is None:
            return default
        expires_at, value = entry
        if time.time() > expires_at:
            del self._store[self._full_key(session_id, key)]
            return default
        return value

    def delete(self, session_id: str, key: str):
        self._store.pop(self._full_key(session_id, key), None)

    def clear_session(self, session_id: str):
        """Called when a session/task ends — wipes all of its scratch space."""
        prefix = f"{session_id}:"
        for k in [k for k in self._store if k.startswith(prefix)]:
            del self._store[k]

    def sweep_expired(self) -> int:
        """Housekeeping pass — removes expired entries proactively rather
        than waiting for them to be looked up. Returns count removed."""
        now = time.time()
        expired = [k for k, (exp, _) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        return len(expired)

    def stats(self) -> dict:
        return {"active_keys": len(self._store)}
