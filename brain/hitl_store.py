"""Durable HITL approval records — not a second auth system.

UCIP still decides capability. This only persists AWAITING_APPROVAL state
so browser disconnect / API restart does not lose the request.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("devos.hitl")

_MEM: dict[str, dict[str, Any]] = {}
_DIR = Path("data/hitl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(approval_id: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / f"{approval_id}.json"


def create_approval(
    *,
    execution_id: str,
    mission_id: str,
    node_id: str | None,
    user_id: str,
    requested_action: str,
    risk: str = "high",
    required_capability: str | None = None,
) -> dict[str, Any]:
    approval_id = str(uuid.uuid4())
    rec = {
        "approval_id": approval_id,
        "execution_id": execution_id,
        "mission_id": mission_id,
        "node_id": node_id,
        "user_id": user_id,
        "requested_action": requested_action,
        "risk": risk,
        "required_capability": required_capability,
        "requested_at": _now(),
        "status": "AWAITING_APPROVAL",
        "approved_by": None,
        "approved_at": None,
        "decision": None,
    }
    _MEM[approval_id] = rec
    try:
        _path(approval_id).write_text(json.dumps(rec, indent=2))
    except Exception as e:
        logger.warning("hitl persist failed: %s", e)
    return rec


def get_approval(approval_id: str) -> Optional[dict[str, Any]]:
    if approval_id in _MEM:
        return dict(_MEM[approval_id])
    try:
        p = _path(approval_id)
        if p.exists():
            rec = json.loads(p.read_text())
            _MEM[approval_id] = rec
            return dict(rec)
    except Exception:
        pass
    return None


def list_pending_for_user(user_id: str) -> list[dict[str, Any]]:
    out = []
    for rec in list(_MEM.values()):
        if rec.get("user_id") == user_id and rec.get("status") == "AWAITING_APPROVAL":
            out.append(dict(rec))
    try:
        if _DIR.exists():
            for f in _DIR.glob("*.json"):
                try:
                    rec = json.loads(f.read_text())
                    if rec.get("user_id") == user_id and rec.get("status") == "AWAITING_APPROVAL":
                        if not any(r["approval_id"] == rec["approval_id"] for r in out):
                            out.append(rec)
                except Exception:
                    continue
    except Exception:
        pass
    return out


def decide_approval(
    approval_id: str,
    *,
    decision: str,
    decided_by: str,
) -> Optional[dict[str, Any]]:
    """decision: APPROVED | DENIED"""
    rec = get_approval(approval_id)
    if not rec:
        return None
    if rec.get("status") != "AWAITING_APPROVAL":
        return rec  # idempotent
    dec = (decision or "").upper()
    if dec not in ("APPROVED", "DENIED"):
        raise ValueError("decision must be APPROVED or DENIED")
    rec["status"] = dec
    rec["decision"] = dec
    rec["approved_by"] = decided_by
    rec["approved_at"] = _now()
    _MEM[approval_id] = rec
    try:
        _path(approval_id).write_text(json.dumps(rec, indent=2))
    except Exception as e:
        logger.warning("hitl decide persist failed: %s", e)
    return rec
