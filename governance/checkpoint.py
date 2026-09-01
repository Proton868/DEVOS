"""
DevOS Checkpoint System — Gap #4: State Durability & Recovery.
═══════════════════════════════════════════════════════════════════════════════
Saves agent loop state after every step so it can resume after crash/restart.
  - Idempotent: safe to re-run a step that already ran
  - Atomic writes: checkpoint written before step, committed after
  - JSON on disk (data/checkpoints/) — no external DB needed
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.checkpoint")

CHECKPOINT_DIR = Path("data/checkpoints")
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


class CheckpointManager:
    """
    Saves and restores loop state.
    One JSON file per loop run at data/checkpoints/{loop_id}.json
    """

    def save(self, loop_id: str, state: dict):
        """Atomically save loop state checkpoint."""
        path = CHECKPOINT_DIR / f"{loop_id}.json"
        tmp  = path.with_suffix(".tmp")
        payload = {
            "loop_id":    loop_id,
            "saved_at":   datetime.now(timezone.utc).isoformat(),
            "state":      state,
        }
        tmp.write_text(json.dumps(payload, default=str, indent=2))
        tmp.replace(path)   # Atomic rename

    def load(self, loop_id: str) -> Optional[dict]:
        """Load checkpoint for a loop. Returns None if not found."""
        path = CHECKPOINT_DIR / f"{loop_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return data.get("state")
        except Exception as e:
            logger.warning(f"Checkpoint load failed for {loop_id}: {e}")
            return None

    def delete(self, loop_id: str):
        """Delete checkpoint after successful completion."""
        path = CHECKPOINT_DIR / f"{loop_id}.json"
        path.unlink(missing_ok=True)

    def list_incomplete(self) -> list[dict]:
        """List all checkpoints that can be resumed."""
        result = []
        for f in CHECKPOINT_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                result.append({
                    "loop_id":  data.get("loop_id"),
                    "saved_at": data.get("saved_at"),
                    "goal":     data.get("state", {}).get("goal", ""),
                    "iteration": data.get("state", {}).get("iteration", 0),
                })
            except Exception:
                pass
        result.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
        return result

    def cleanup_old(self, max_age_hours: int = 48):
        """Remove checkpoints older than max_age_hours."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        for f in CHECKPOINT_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                saved = datetime.fromisoformat(data.get("saved_at", "2000-01-01"))
                if saved < cutoff:
                    f.unlink()
            except Exception:
                pass
