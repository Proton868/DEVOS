"""Delivery cancel cascade — in-process flags (not domain SoT)."""
from __future__ import annotations

import threading
from typing import Optional

_LOCK = threading.Lock()
_CANCELLED: set[str] = set()


def bind_delivery(delivery_id: str, **meta) -> None:
    return


def request_delivery_cancel(delivery_id: str) -> None:
    with _LOCK:
        _CANCELLED.add(delivery_id)


def is_delivery_cancelled(delivery_id: str) -> bool:
    with _LOCK:
        return delivery_id in _CANCELLED


def clear_delivery_cancel(delivery_id: str) -> None:
    with _LOCK:
        _CANCELLED.discard(delivery_id)


def cascade_cancel_plan(plan_id: str) -> None:
    request_delivery_cancel(plan_id)
