"""Atomic JSON write helper."""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any

def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str, indent=2))
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def read_json(path: Path):
    path = Path(path)
    if not path.exists(): return None
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None
