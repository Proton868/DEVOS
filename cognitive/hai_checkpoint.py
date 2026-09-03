"""Durable HAI checkpoints + reconciliation (Stage 3H/3I)."""
from __future__ import annotations
import hashlib, json, logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("devos.hai_checkpoint")
HAI_CHECKPOINT_SCHEMA = 1
MAX_SUBGOALS, MAX_OBS, MAX_ACTIONS, MAX_TEXT = 32, 32, 48, 2000
_FORBIDDEN = frozenset({
    "authority","capabilities","capability","tenant","tenant_id","user","user_id","owner_id","actor_id",
    "role","roles","is_admin","trust_level","sandbox","sandbox_policy","secret","secrets","token","api_key",
    "password","credential","credentials","jwt","approval","identity",
})

class CheckpointError(ValueError): pass

class ReconcileOutcome(str, Enum):
    CONTINUE="continue"; REPLAN="replan"; VERIFY="verify"; COMPLETED="completed"
    FAILED="failed"; BLOCKED="blocked"; UNKNOWN="unknown"; CANCELLED="cancelled"; WAIT="wait"

def _now_iso(): return datetime.now(timezone.utc).isoformat()
def _clip(t, n=MAX_TEXT):
    if t is None: return ""
    s=str(t); return s if len(s)<=n else s[:n-3]+"..."

def strip_forbidden(data):
    if isinstance(data, dict):
        out={}
        for k,v in data.items():
            key=str(k).lower()
            if key in _FORBIDDEN or any(x in key for x in ("secret","token","password","api_key","credential","authority","capability")):
                if key in ("purpose","status","description","reason_code","tool","arguments","summary","decision"):
                    out[k]=strip_forbidden(v)
                continue
            out[k]=strip_forbidden(v)
        return out
    if isinstance(data, list): return [strip_forbidden(x) for x in data[:64]]
    if isinstance(data, str): return _clip(data)
    return data

def _checksum(payload):
    body={k:v for k,v in payload.items() if k!="checksum"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",",":"), default=str).encode()).hexdigest()[:32]

def bound_state(state):
    s=strip_forbidden(dict(state or {}))
    strat=dict(s.get("strategic") or {})
    strat["subgoals"]=list(strat.get("subgoals") or [])[:MAX_SUBGOALS]
    strat["plan"]=_clip(strat.get("plan"), 8000)
    strat["objective"]=_clip(strat.get("objective"), 2000)
    strat["completed_subgoals"]=list(strat.get("completed_subgoals") or [])[:MAX_SUBGOALS]
    strat["blockers"]=[_clip(b,300) for b in list(strat.get("blockers") or [])[:16]]
    s["strategic"]=strat
    tac=dict(s.get("tactical") or {})
    tac["observations"]=list(tac.get("observations") or [])[-MAX_OBS:]
    tac["tool_calls"]=list(tac.get("tool_calls") or [])[-MAX_ACTIONS:]
    tac["action_history"]=list(tac.get("action_history") or [])[-MAX_ACTIONS:]
    s["tactical"]=tac
    for k in ("user_id","tenant_id","owner_id","capabilities","authority"): s.pop(k, None)
    return s

@dataclass
class HAICheckpoint:
    schema_version: int = HAI_CHECKPOINT_SCHEMA
    task_id: str = ""
    state_version: int = 0
    created_at: str = field(default_factory=_now_iso)
    lifecycle: str = "created"
    state: dict = field(default_factory=dict)
    last_job_id: Optional[str] = None
    last_job_status: Optional[str] = None
    last_operation_id: Optional[str] = None
    workflow_id: Optional[str] = None
    correlation_id: str = ""
    checksum: str = ""
    def to_dict(self):
        payload={
            "schema_version": int(self.schema_version), "task_id": self.task_id,
            "state_version": int(self.state_version), "created_at": self.created_at,
            "lifecycle": self.lifecycle, "state": bound_state(self.state),
            "last_job_id": self.last_job_id, "last_job_status": self.last_job_status,
            "last_operation_id": self.last_operation_id,
            "workflow_id": self.workflow_id, "correlation_id": self.correlation_id,
        }
        payload["checksum"]=_checksum(payload); self.checksum=payload["checksum"]; return payload
    @classmethod
    def from_dict(cls, data, *, verify=True):
        if not isinstance(data, dict): raise CheckpointError("checkpoint must be a dict")
        data=dict(data)
        if int(data.get("schema_version") or 0)!=HAI_CHECKPOINT_SCHEMA:
            raise CheckpointError(f"unsupported schema_version: {data.get('schema_version')}")
        if verify and data.get("checksum")!=_checksum(data):
            raise CheckpointError("checksum mismatch")
        state=bound_state(data.get("state") or {})
        for k in ("user_id","tenant_id","owner_id","capabilities","authority"):
            if k in data: raise CheckpointError(f"forbidden identity field in checkpoint: {k}")
        return cls(schema_version=HAI_CHECKPOINT_SCHEMA, task_id=str(data.get("task_id") or ""),
                   state_version=int(data.get("state_version") or 0),
                   created_at=str(data.get("created_at") or _now_iso()),
                   lifecycle=str(data.get("lifecycle") or "created"), state=state,
                   last_job_id=data.get("last_job_id"), last_job_status=data.get("last_job_status"),
                   last_operation_id=data.get("last_operation_id"),
                   workflow_id=data.get("workflow_id"), correlation_id=str(data.get("correlation_id") or ""),
                   checksum=str(data.get("checksum") or ""))

def build_checkpoint(*, task_id, state, lifecycle="executing", state_version=0,
                     last_job_id=None, last_job_status=None, last_operation_id=None,
                     workflow_id=None, correlation_id=""):
    return HAICheckpoint(task_id=task_id, state=bound_state(state), lifecycle=lifecycle,
                         state_version=state_version, last_job_id=last_job_id,
                         last_job_status=last_job_status, last_operation_id=last_operation_id,
                         workflow_id=workflow_id,
                         correlation_id=correlation_id, created_at=_now_iso())

@dataclass
class Reconciliation:
    outcome: str; lifecycle: str; summary: str
    retry: bool=False; job_id: Optional[str]=None; job_status: Optional[str]=None
    def to_dict(self):
        return {"outcome":self.outcome,"lifecycle":self.lifecycle,"summary":self.summary,
                "retry":bool(self.retry),"job_id":self.job_id,"job_status":self.job_status}

def reconcile_with_execution(checkpoint, *, job_status=None, job_id=None, task_status=None):
    ts=(task_status or "").lower()
    if ts in ("cancelled","canceled"):
        return Reconciliation(ReconcileOutcome.CANCELLED.value,"cancelled","Task cancelled — no autonomous restart",False,job_id,job_status)
    js=(job_status or checkpoint.last_job_status or "").lower()
    jid=job_id or checkpoint.last_job_id
    if js=="unknown":
        return Reconciliation(ReconcileOutcome.UNKNOWN.value,"unknown","ExecutionJob UNKNOWN — will not blind-retry",False,jid,"unknown")
    if js in ("running","queued","pending","pending_approval"):
        return Reconciliation(ReconcileOutcome.WAIT.value,"waiting",f"ExecutionJob still {js} — do not duplicate",False,jid,js)
    if js in ("succeeded","success","ok"):
        return Reconciliation(ReconcileOutcome.CONTINUE.value,"executing","Execution succeeded — do not repeat side effect",False,jid,js)
    if js in ("failed","error"):
        return Reconciliation(ReconcileOutcome.REPLAN.value,"replanning","Execution failed — replan under existing policy",False,jid,js)
    life=(checkpoint.lifecycle or "").lower()
    if life in ("succeeded","completed"):
        return Reconciliation(ReconcileOutcome.COMPLETED.value,"succeeded","Checkpoint already terminal success",False)
    if life in ("failed","blocked","cancelled"):
        return Reconciliation(life,life,f"Checkpoint terminal: {life}",False)
    return Reconciliation(ReconcileOutcome.CONTINUE.value, life or "executing","Continue from checkpoint",False,jid,js or None)
