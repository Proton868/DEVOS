#!/usr/bin/env python3
"""Live production proof harness for DevOS P0 gates.

  export DEVOS_BASE_URL=https://devos.carai.agency
  export DEVOS_TOKEN=<jwt>
  python scripts/live_production_proof.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DEVOS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("DEVOS_TOKEN", "")


def req(method, path, body=None, timeout=30.0):
    data = None
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")


def main():
    results = []

    def gate(name, ok, evidence):
        results.append((name, "PASS" if ok else "FAIL", evidence))
        print(f"{name}: {'PASS' if ok else 'FAIL'} — {evidence}")

    code, headers, body = req("GET", "/api/health")
    is_devos = False
    try:
        j = json.loads(body)
        is_devos = j.get("service") == "devos" or "status" in j
        if "PyRunner" in body:
            is_devos = False
    except Exception:
        j = {}
    gate(
        "health_devos",
        code == 200 and is_devos and "PyRunner" not in body,
        f"http={code} service={j.get('service')} prefix={body[:80]!r}",
    )

    if not TOKEN:
        gate("auth_token", False, "DEVOS_TOKEN not set")
        print("\nSet DEVOS_TOKEN for authenticated gates.")
        for n, s, e in results:
            print(f"| {n} | {s} | {e} |")
        return 1

    code, _, body = req(
        "POST",
        "/api/orchestration/run?background=1",
        {
            "goal": "Create a tiny calculator module with add() and a unit test, then verify.",
            "background": True,
        },
    )
    plan_id = None
    try:
        j = json.loads(body)
        plan_id = j.get("execution_id") or j.get("plan_id")
    except Exception:
        pass
    gate("background_run", code in (200, 201) and bool(plan_id), f"http={code} plan_id={plan_id}")

    if plan_id:
        code, _, body = req("GET", f"/api/orchestration/{plan_id}/events?after=0")
        try:
            n = len(json.loads(body).get("events") or [])
        except Exception:
            n = -1
        gate("events_replay", code == 200 and n >= 0, f"http={code} events={n}")
        code, _, _ = req("POST", f"/api/orchestration/{plan_id}/cancel")
        gate("cancel", code == 200, f"http={code}")

    gate("cross_user_sse", False, "requires second user token — run manually")
    print("\n=== SUMMARY ===")
    for n, s, e in results:
        print(f"| {n} | {s} | {e} |")
    return 0 if all(r[1] == "PASS" for r in results if r[0] != "cross_user_sse") else 1


if __name__ == "__main__":
    sys.exit(main())
