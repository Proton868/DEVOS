"""
DevOS Observability Layer — Gap #5 from audit.
═══════════════════════════════════════════════════════════════════════════════
Provides:
  - Structured trace per agent loop run (full replay capability)
  - Metrics: latency, token usage, success rate, cost per task
  - Tool call log with: agent_id, task_id, tool, latency, tokens, result, error_type
  - Step-by-step replay for debugging
  - In-memory store with SQLite persistence (no external infra required)
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("devos.obs")

OBS_DB = Path("data/observability.db")


@dataclass
class ToolCallRecord:
    """One tool invocation — the atomic unit of observability."""
    record_id:    str
    trace_id:     str    # = loop run ID
    agent_id:     str
    task_id:      str    # = session_id
    tool_name:    str
    tool_input:   str    # Truncated
    tool_output:  str    # Truncated
    status:       str    # success | failed | denied | timeout
    decision:     str    # UCIP decision: APPROVE | DENY | ESCALATE
    latency_ms:   int
    tokens_used:  int
    error_type:   Optional[str]
    iteration:    int
    timestamp:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "record_id":   self.record_id,
            "trace_id":    self.trace_id,
            "agent_id":    self.agent_id,
            "task_id":     self.task_id,
            "tool_name":   self.tool_name,
            "tool_input":  self.tool_input[:200],
            "tool_output": self.tool_output[:500],
            "status":      self.status,
            "decision":    self.decision,
            "latency_ms":  self.latency_ms,
            "tokens_used": self.tokens_used,
            "error_type":  self.error_type,
            "iteration":   self.iteration,
            "timestamp":   self.timestamp.isoformat(),
        }


@dataclass
class TraceRecord:
    """Full trace of one Brain loop run."""
    trace_id:    str
    agent_id:    str
    task_id:     str
    goal:        str
    provider:    str
    model:       str
    status:      str    # running | complete | failed | aborted | escalated
    decision:    str    # UCIP final decision
    iterations:  int
    total_tokens: int
    total_latency_ms: int
    tool_calls:  int
    final_answer: str
    started_at:  datetime
    finished_at: Optional[datetime] = None
    error:       Optional[str] = None

    def duration_s(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    def to_dict(self) -> dict:
        return {
            "trace_id":        self.trace_id,
            "agent_id":        self.agent_id,
            "task_id":         self.task_id,
            "goal":            self.goal[:200],
            "provider":        self.provider,
            "model":           self.model,
            "status":          self.status,
            "decision":        self.decision,
            "iterations":      self.iterations,
            "total_tokens":    self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "tool_calls":      self.tool_calls,
            "duration_s":      self.duration_s(),
            "final_answer":    self.final_answer[:300],
            "started_at":      self.started_at.isoformat(),
            "finished_at":     self.finished_at.isoformat() if self.finished_at else None,
            "error":           self.error,
        }


class ObservabilityStore:
    """
    Persistent observability store backed by SQLite.
    No external dependencies — works on Termux.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        OBS_DB.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(OBS_DB), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS traces (
                trace_id       TEXT PRIMARY KEY,
                agent_id       TEXT,
                task_id        TEXT,
                goal           TEXT,
                provider       TEXT,
                model          TEXT,
                status         TEXT,
                decision       TEXT,
                iterations     INTEGER DEFAULT 0,
                total_tokens   INTEGER DEFAULT 0,
                total_latency_ms INTEGER DEFAULT 0,
                tool_calls     INTEGER DEFAULT 0,
                final_answer   TEXT,
                error          TEXT,
                started_at     TEXT,
                finished_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS tool_calls (
                record_id    TEXT PRIMARY KEY,
                trace_id     TEXT,
                agent_id     TEXT,
                task_id      TEXT,
                tool_name    TEXT,
                tool_input   TEXT,
                tool_output  TEXT,
                status       TEXT,
                decision     TEXT,
                latency_ms   INTEGER DEFAULT 0,
                tokens_used  INTEGER DEFAULT 0,
                error_type   TEXT,
                iteration    INTEGER DEFAULT 0,
                timestamp    TEXT,
                FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
            );

            CREATE TABLE IF NOT EXISTS errors (
                error_id      TEXT PRIMARY KEY,
                component     TEXT,
                message       TEXT,
                trace_id      TEXT,
                user_id       TEXT,
                status_code   INTEGER DEFAULT 500,
                severity      TEXT DEFAULT 'error',
                timestamp     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tc_trace ON tool_calls(trace_id);
            CREATE INDEX IF NOT EXISTS idx_tc_agent ON tool_calls(agent_id);
            CREATE INDEX IF NOT EXISTS idx_tr_agent ON traces(agent_id);
            CREATE INDEX IF NOT EXISTS idx_tr_task  ON traces(task_id);
            CREATE INDEX IF NOT EXISTS idx_err_component ON errors(component);
        """)
        self._conn.commit()

    # ── Traces ────────────────────────────────────────────────────────────────

    def start_trace(self, trace_id: str, agent_id: str, task_id: str,
                    goal: str, provider: str, model: str) -> TraceRecord:
        trace = TraceRecord(
            trace_id=trace_id, agent_id=agent_id, task_id=task_id,
            goal=goal, provider=provider, model=model,
            status="running", decision="pending",
            iterations=0, total_tokens=0, total_latency_ms=0,
            tool_calls=0, final_answer="",
            started_at=datetime.now(timezone.utc),
        )
        self._conn.execute("""
            INSERT OR REPLACE INTO traces
            (trace_id, agent_id, task_id, goal, provider, model, status, decision,
             iterations, total_tokens, total_latency_ms, tool_calls, final_answer, started_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (trace.trace_id, trace.agent_id, trace.task_id, trace.goal[:500],
              trace.provider, trace.model, trace.status, trace.decision,
              trace.iterations, trace.total_tokens, trace.total_latency_ms,
              trace.tool_calls, trace.final_answer,
              trace.started_at.isoformat()))
        self._conn.commit()
        return trace

    def finish_trace(self, trace_id: str, status: str, decision: str,
                     iterations: int, total_tokens: int, total_latency_ms: int,
                     tool_calls: int, final_answer: str, error: Optional[str] = None):
        self._conn.execute("""
            UPDATE traces SET status=?, decision=?, iterations=?, total_tokens=?,
            total_latency_ms=?, tool_calls=?, final_answer=?, error=?, finished_at=?
            WHERE trace_id=?
        """, (status, decision, iterations, total_tokens, total_latency_ms,
              tool_calls, final_answer[:1000], error,
              datetime.now(timezone.utc).isoformat(), trace_id))
        self._conn.commit()

    def get_trace(self, trace_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM traces WHERE trace_id=?", (trace_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def list_traces(self, task_id: Optional[str] = None,
                    agent_id: Optional[str] = None,
                    limit: int = 50) -> list[dict]:
        query = "SELECT * FROM traces"
        params = []
        conditions = []
        if task_id:
            conditions.append("task_id=?"); params.append(task_id)
        if agent_id:
            conditions.append("agent_id=?"); params.append(agent_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ── Tool Calls ────────────────────────────────────────────────────────────

    def record_tool_call(self, trace_id: str, agent_id: str, task_id: str,
                         tool_name: str, tool_input: str, tool_output: str,
                         status: str, decision: str, latency_ms: int,
                         tokens_used: int = 0, error_type: Optional[str] = None,
                         iteration: int = 0) -> str:
        record_id = str(uuid.uuid4())
        self._conn.execute("""
            INSERT INTO tool_calls
            (record_id, trace_id, agent_id, task_id, tool_name, tool_input,
             tool_output, status, decision, latency_ms, tokens_used, error_type,
             iteration, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (record_id, trace_id, agent_id, task_id, tool_name,
              tool_input[:300], tool_output[:600],
              status, decision, latency_ms, tokens_used,
              error_type, iteration, datetime.now(timezone.utc).isoformat()))
        self._conn.commit()
        return record_id

    def get_tool_calls(self, trace_id: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM tool_calls WHERE trace_id=? ORDER BY timestamp",
            (trace_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def record_error(self, component: str, message: str, trace_id: Optional[str] = None,
                     user_id: Optional[str] = None, status_code: int = 500,
                     severity: str = "error") -> dict:
        error_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        self._conn.execute("""
            INSERT INTO errors (error_id, component, message, trace_id, user_id, status_code, severity, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (error_id, component, message[:2000], trace_id, user_id, status_code, severity, ts))
        self._conn.commit()
        return {"error_id": error_id, "component": component, "message": message[:2000], "trace_id": trace_id,
                "user_id": user_id, "status_code": status_code, "severity": severity, "timestamp": ts}

    def list_errors(self, limit: int = 50) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM errors ORDER BY timestamp DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def replay_trace(self, trace_id: str) -> dict:
        """Return full trace + tool calls for step-by-step replay."""
        trace = self.get_trace(trace_id)
        if not trace:
            return {"error": "trace not found"}
        calls = self.get_tool_calls(trace_id)
        return {
            "trace":      trace,
            "tool_calls": calls,
            "replay_steps": [
                {
                    "step": i + 1,
                    "tool": c["tool_name"],
                    "decision": c["decision"],
                    "status": c["status"],
                    "latency_ms": c["latency_ms"],
                    "summary": f"{c['tool_name']} → {c['status']} ({c['latency_ms']}ms)",
                }
                for i, c in enumerate(calls)
            ],
        }

    # ── Metrics ───────────────────────────────────────────────────────────────

    def metrics(self, task_id: Optional[str] = None) -> dict:
        """Aggregate metrics for dashboard."""
        base = "WHERE task_id=?" if task_id else ""
        params = [task_id] if task_id else []

        def q(sql): return self._conn.execute(sql, params).fetchone()[0] or 0

        total   = q(f"SELECT COUNT(*) FROM traces {base}")
        done    = q(f"SELECT COUNT(*) FROM traces {base} {'AND' if base else 'WHERE'} status='complete'" if base else f"SELECT COUNT(*) FROM traces WHERE status='complete'")
        failed  = q(f"SELECT COUNT(*) FROM traces WHERE status='failed'" + (f" AND task_id=?" if task_id else ""))
        aborted = q(f"SELECT COUNT(*) FROM traces WHERE status='aborted'" + (f" AND task_id=?" if task_id else ""))

        avg_iters_row = self._conn.execute(
            f"SELECT AVG(iterations) FROM traces {'WHERE task_id=?' if task_id else ''}",
            [task_id] if task_id else []).fetchone()
        avg_iters = round(avg_iters_row[0] or 0, 2)

        total_tokens = q(f"SELECT SUM(total_tokens) FROM traces {'WHERE task_id=?' if task_id else ''}")
        denied_calls = q(f"SELECT COUNT(*) FROM tool_calls WHERE decision='DENY'" + (f" AND task_id=?" if task_id else ""))
        hitl_calls   = q(f"SELECT COUNT(*) FROM tool_calls WHERE decision='ESCALATE_TO_HUMAN'" + (f" AND task_id=?" if task_id else ""))

        success_rate = round(done / total * 100, 1) if total else 0

        error_count = self._conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0] or 0

        return {
            "total_runs":     total,
            "completed":      done,
            "failed":         failed,
            "aborted":        aborted,
            "success_rate_%": success_rate,
            "avg_iterations": avg_iters,
            "total_tokens":   total_tokens,
            "denied_calls":   denied_calls,
            "hitl_escalations": hitl_calls,
            "error_count":    error_count,
        }
