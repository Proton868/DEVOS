#!/usr/bin/env python3
"""Independent external provider stub for P1 drop-response staging.

POST /execute  — accept side effect, optionally drop HTTP response
GET  /status/{operation_key} — provider-side truth
GET  /health

Env:
  P1_PROVIDER_PORT=8099
  P1_DROP_RESPONSE=true   # drop response after accepting (first/later controlled)
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("P1_PROVIDER_PORT", "8099"))
DROP = os.environ.get("P1_DROP_RESPONSE", "true").lower() in ("1", "true", "yes")

_lock = threading.Lock()
# operation_key -> state
_state: dict[str, dict] = {}


def _record_accept(operation_key: str, payload: dict) -> dict:
    with _lock:
        cur = _state.get(operation_key)
        if cur is not None:
            # Idempotent: do not double side effect
            cur["execution_count"] = int(cur.get("execution_count") or 0) + 1
            return dict(cur)
        row = {
            "operation_key": operation_key,
            "accepted": True,
            "execution_count": 1,
            "side_effect_count": 1,
            "payload": payload,
            "status": "success",
            "id": f"ext-{operation_key[:12]}",
        }
        _state[operation_key] = row
        return dict(row)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[provider-stub] {self.address_string()} {fmt % args}")

    def _json(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"ok": True, "drop_response": DROP})
        if path.startswith("/status/"):
            key = path[len("/status/") :]
            with _lock:
                row = _state.get(key)
            if not row:
                return self._json(404, {"operation_key": key, "accepted": False, "execution_count": 0, "side_effect_count": 0})
            return self._json(200, dict(row))
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/execute":
            return self._json(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except Exception:
            return self._json(400, {"error": "invalid_json"})
        key = body.get("operation_key") or body.get("idempotency_key")
        if not key:
            return self._json(400, {"error": "operation_key required"})
        payload = body.get("payload") or {}
        # Accept + record BEFORE any response path
        row = _record_accept(str(key), payload)
        drop = body.get("drop_response")
        if drop is None:
            drop = DROP
        if drop:
            # Deliberately drop response: close without body after accept
            try:
                self.close_connection = True
                # Half-close: no status line — client sees connection error
                self.connection.close()
            except Exception:
                pass
            return
        return self._json(200, {
            "status": "success",
            "id": row["id"],
            "operation_key": key,
            "http_status": 200,
        })


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[provider-stub] listening on 0.0.0.0:{PORT} drop={DROP}")
    server.serve_forever()


if __name__ == "__main__":
    main()
