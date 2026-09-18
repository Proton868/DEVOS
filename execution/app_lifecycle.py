"""
Application Lifecycle — CREATE → … → MAINTAIN across IDE / Flow / Runtime / Nuha.

Rules
-----
- Stage transitions emit structured evidence; claims require evidence.
- DEPLOY is successful only when a DeploymentAdapter returns DEPLOYED *and*
  verification evidence is present (or provider reports verified deploy).
- No fake deployment success.
- Nuha/Flow/IDE request stages through UCIP-backed capabilities; this module
  does not grant authority.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from execution.app_detect import detect_application
from execution.app_runtime import AppRuntimeState
from execution.artifacts import write_bytes
from execution.deploy.base import DeploymentStatus
from execution.files import FileService
from execution.runtime_service import (
    ProjectRuntimeSnapshot,
    get_or_create_runtime,
    probe_health,
    run_lifecycle_action,
    snapshot,
)

logger = logging.getLogger("devos.app_lifecycle")


class LifecycleStage(str, Enum):
    CREATE = "CREATE"
    DEVELOP = "DEVELOP"
    TEST = "TEST"
    BUILD = "BUILD"
    PREVIEW = "PREVIEW"
    VERIFY = "VERIFY"
    DEPLOY = "DEPLOY"
    OBSERVE = "OBSERVE"
    MAINTAIN = "MAINTAIN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Allowed forward progress (FAILED/CANCELLED terminal unless explicit reset)
_TRANSITIONS: dict[LifecycleStage, set[LifecycleStage]] = {
    LifecycleStage.CREATE: {LifecycleStage.DEVELOP, LifecycleStage.FAILED, LifecycleStage.CANCELLED},
    LifecycleStage.DEVELOP: {
        LifecycleStage.TEST, LifecycleStage.BUILD, LifecycleStage.DEVELOP,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.TEST: {
        LifecycleStage.BUILD, LifecycleStage.DEVELOP, LifecycleStage.TEST,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.BUILD: {
        LifecycleStage.PREVIEW, LifecycleStage.TEST, LifecycleStage.BUILD,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.PREVIEW: {
        LifecycleStage.VERIFY, LifecycleStage.BUILD, LifecycleStage.PREVIEW,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.VERIFY: {
        LifecycleStage.DEPLOY, LifecycleStage.PREVIEW, LifecycleStage.OBSERVE,
        LifecycleStage.VERIFY, LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.DEPLOY: {
        LifecycleStage.OBSERVE, LifecycleStage.VERIFY, LifecycleStage.DEPLOY,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.OBSERVE: {
        LifecycleStage.MAINTAIN, LifecycleStage.OBSERVE, LifecycleStage.DEVELOP,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.MAINTAIN: {
        LifecycleStage.DEVELOP, LifecycleStage.OBSERVE, LifecycleStage.MAINTAIN,
        LifecycleStage.FAILED, LifecycleStage.CANCELLED,
    },
    LifecycleStage.FAILED: {LifecycleStage.DEVELOP, LifecycleStage.CREATE},  # recovery
    LifecycleStage.CANCELLED: {LifecycleStage.CREATE},
}


@dataclass
class StageEvidence:
    stage: str
    ok: bool
    summary: str
    timestamp: float = field(default_factory=time.time)
    details: dict = field(default_factory=dict)
    artifact_refs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "ok": self.ok,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "details": dict(self.details or {}),
            "artifact_refs": list(self.artifact_refs or []),
        }


@dataclass
class LifecycleRecord:
    lifecycle_id: str
    user_id: str
    project_id: str
    stage: LifecycleStage
    history: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    runtime: Optional[dict] = None
    deployment: Optional[dict] = None
    verification: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancelled: bool = False
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "lifecycle_id": self.lifecycle_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "stage": self.stage.value if isinstance(self.stage, LifecycleStage) else self.stage,
            "history": list(self.history or []),
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in (self.evidence or [])],
            "runtime": self.runtime,
            "deployment": self.deployment,
            "verification": self.verification,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cancelled": self.cancelled,
            "meta": dict(self.meta or {}),
        }


# Process-local store (durable persistence optional via durable_store upsert)
_RECORDS: dict[str, LifecycleRecord] = {}
_BY_PROJECT: dict[str, str] = {}  # user_id:project_id → lifecycle_id


def _key(user_id: str, project_id: str) -> str:
    return f"{user_id}:{project_id}"


def _new_id() -> str:
    return f"lc_{uuid.uuid4().hex[:16]}"


def get_lifecycle(user_id: str, project_id: str) -> Optional[LifecycleRecord]:
    lid = _BY_PROJECT.get(_key(user_id, project_id))
    if not lid:
        return None
    return _RECORDS.get(lid)


def get_lifecycle_by_id(lifecycle_id: str) -> Optional[LifecycleRecord]:
    return _RECORDS.get(lifecycle_id)


def list_evidence(user_id: str, project_id: str) -> list[dict]:
    rec = get_lifecycle(user_id, project_id)
    if not rec:
        return []
    return [e.to_dict() if hasattr(e, "to_dict") else e for e in rec.evidence]


def create_lifecycle(
    user_id: str,
    project_id: str,
    *,
    bootstrap_files: Optional[dict[str, bytes]] = None,
    meta: Optional[dict] = None,
) -> LifecycleRecord:
    """CREATE stage: ensure project workspace exists; optional bootstrap files."""
    existing = get_lifecycle(user_id, project_id)
    if existing and existing.stage not in (LifecycleStage.FAILED, LifecycleStage.CANCELLED):
        return existing

    fs = FileService(user_id, project_id)
    wrote: list[str] = []
    if bootstrap_files:
        for path, data in bootstrap_files.items():
            write_bytes(fs, path, data if isinstance(data, (bytes, bytearray)) else str(data).encode())
            wrote.append(path)

    # Detect whether workspace already has content
    detection = detect_application(fs)
    lid = _new_id()
    ev = StageEvidence(
        stage=LifecycleStage.CREATE.value,
        ok=True,
        summary="project lifecycle created",
        details={"detection": detection, "bootstrap": wrote},
        artifact_refs=wrote,
    )
    rec = LifecycleRecord(
        lifecycle_id=lid,
        user_id=user_id,
        project_id=project_id,
        stage=LifecycleStage.CREATE,
        history=[{"stage": LifecycleStage.CREATE.value, "at": time.time(), "ok": True}],
        evidence=[ev],
        meta=dict(meta or {}),
    )
    _RECORDS[lid] = rec
    _BY_PROJECT[_key(user_id, project_id)] = lid
    _persist(rec)
    return rec


def _persist(rec: LifecycleRecord) -> None:
    try:
        from execution.durable_store import upsert_runtime, new_id

        upsert_runtime(
            runtime_id=rec.lifecycle_id,
            user_id=rec.user_id,
            project_id=rec.project_id,
            status=rec.stage.value if isinstance(rec.stage, LifecycleStage) else str(rec.stage),
            meta={"kind": "app_lifecycle", **rec.to_dict()},
        )
    except Exception as e:
        logger.debug("lifecycle persist skipped: %s", type(e).__name__)


def _append_evidence(rec: LifecycleRecord, ev: StageEvidence) -> None:
    rec.evidence.append(ev)
    rec.history.append({"stage": ev.stage, "at": ev.timestamp, "ok": ev.ok, "summary": ev.summary})
    rec.updated_at = time.time()


def _set_stage(rec: LifecycleRecord, stage: LifecycleStage) -> None:
    rec.stage = stage
    rec.updated_at = time.time()


def _can_transition(rec: LifecycleRecord, target: LifecycleStage) -> bool:
    if rec.cancelled and target != LifecycleStage.CREATE:
        return False
    allowed = _TRANSITIONS.get(rec.stage, set())
    return target in allowed or target == rec.stage


async def advance(
    user_id: str,
    project_id: str,
    stage: str,
    *,
    params: Optional[dict] = None,
) -> LifecycleRecord:
    """
    Execute work for a lifecycle stage and record evidence.

    stage: one of LifecycleStage values (case-insensitive).
    """
    params = dict(params or {})
    target = LifecycleStage((stage or "").strip().upper())

    rec = get_lifecycle(user_id, project_id)
    if rec is None:
        rec = create_lifecycle(user_id, project_id, meta={"source": params.get("source") or "api"})

    if target == LifecycleStage.CANCELLED:
        rec.cancelled = True
        _set_stage(rec, LifecycleStage.CANCELLED)
        _append_evidence(
            rec,
            StageEvidence(stage="CANCELLED", ok=True, summary="lifecycle cancelled"),
        )
        _persist(rec)
        return rec

    if not _can_transition(rec, target) and target != LifecycleStage.CREATE:
        _append_evidence(
            rec,
            StageEvidence(
                stage=target.value,
                ok=False,
                summary=f"illegal transition from {rec.stage.value} to {target.value}",
                details={"from": rec.stage.value, "to": target.value},
            ),
        )
        _set_stage(rec, LifecycleStage.FAILED)
        _persist(rec)
        return rec

    if target == LifecycleStage.CREATE:
        return create_lifecycle(user_id, project_id, meta=params)

    if target == LifecycleStage.DEVELOP:
        # Record development activity evidence (file writes happen via IDE/tools)
        fs = FileService(user_id, project_id)
        detection = detect_application(fs)
        paths = params.get("paths") or []
        _append_evidence(
            rec,
            StageEvidence(
                stage="DEVELOP",
                ok=True,
                summary="development activity recorded",
                details={"detection": detection, "paths": paths},
                artifact_refs=list(paths),
            ),
        )
        _set_stage(rec, LifecycleStage.DEVELOP)
        _persist(rec)
        return rec

    if target == LifecycleStage.TEST:
        snap = await run_lifecycle_action(user_id, project_id, "test")
        ok = snap.state in ("READY", "BUILT") and (snap.evidence or {}).get("exit_code", 1) == 0
        # Honest: if no test script, mark failed for test stage (not success)
        if (snap.evidence or {}).get("exit_code") is None and "no test" in (snap.detail or "").lower():
            ok = False
        if snap.state == "FAILED":
            ok = False
        _append_evidence(
            rec,
            StageEvidence(
                stage="TEST",
                ok=ok,
                summary=snap.detail or ("tests passed" if ok else "tests failed"),
                details={"runtime": snap.to_dict()},
            ),
        )
        _set_stage(rec, LifecycleStage.TEST if ok else LifecycleStage.FAILED)
        rec.runtime = snap.to_dict()
        _persist(rec)
        return rec

    if target == LifecycleStage.BUILD:
        # install then build when applicable
        snap_i = await run_lifecycle_action(user_id, project_id, "install")
        if snap_i.state == "FAILED":
            _append_evidence(
                rec,
                StageEvidence(
                    stage="BUILD",
                    ok=False,
                    summary=snap_i.detail or "install failed",
                    details={"runtime": snap_i.to_dict()},
                ),
            )
            _set_stage(rec, LifecycleStage.FAILED)
            rec.runtime = snap_i.to_dict()
            _persist(rec)
            return rec
        snap_b = await run_lifecycle_action(user_id, project_id, "build")
        # Static sites may be READY/BUILT without build command
        ok = snap_b.state not in ("FAILED",)
        if snap_b.state == "UNSUPPORTED" and (snap_b.detection or {}).get("kind") == "STATIC_SITE":
            ok = True
        if snap_b.state in ("BUILT", "READY", "STOPPED") and "failed" not in (snap_b.detail or "").lower():
            ok = True
        if snap_b.state == "FAILED":
            ok = False
        # Prefer explicit failure
        if snap_i.state == "FAILED":
            ok = False
        _append_evidence(
            rec,
            StageEvidence(
                stage="BUILD",
                ok=ok,
                summary=snap_b.detail or ("build ok" if ok else "build failed"),
                details={"install": snap_i.to_dict(), "build": snap_b.to_dict()},
            ),
        )
        _set_stage(rec, LifecycleStage.BUILD if ok else LifecycleStage.FAILED)
        rec.runtime = snap_b.to_dict()
        _persist(rec)
        return rec

    if target == LifecycleStage.PREVIEW:
        snap = await run_lifecycle_action(user_id, project_id, "start")
        kind = (snap.detection or {}).get("kind")
        ok = snap.state in ("READY", "BUILT") or (
            kind == "STATIC_SITE" and snap.state not in ("FAILED",)
        )
        if snap.state == "FAILED":
            ok = False
        _append_evidence(
            rec,
            StageEvidence(
                stage="PREVIEW",
                ok=ok,
                summary=snap.detail or ("preview ready" if ok else "preview failed"),
                details={"runtime": snap.to_dict()},
            ),
        )
        _set_stage(rec, LifecycleStage.PREVIEW if ok else LifecycleStage.FAILED)
        rec.runtime = snap.to_dict()
        _persist(rec)
        return rec

    if target == LifecycleStage.VERIFY:
        snap = snapshot(user_id, project_id, probe=True)
        health = snap.health
        kind = (snap.detection or {}).get("kind")
        # Static preview via file service is verifiable without process
        if kind == "STATIC_SITE":
            fs = FileService(user_id, project_id)
            try:
                fs.read("index.html")
                ok = True
                health = "healthy"
                detail = "static entrypoint present"
            except Exception as e:
                ok = False
                detail = f"static entry missing: {e}"
        else:
            ok = health == "healthy" or snap.state == "READY"
            detail = f"health={health} state={snap.state}"
        verification = {
            "ok": ok,
            "health": health,
            "state": snap.state,
            "detail": detail,
            "at": time.time(),
        }
        rec.verification = verification
        _append_evidence(
            rec,
            StageEvidence(
                stage="VERIFY",
                ok=ok,
                summary=detail,
                details={"verification": verification, "runtime": snap.to_dict()},
            ),
        )
        _set_stage(rec, LifecycleStage.VERIFY if ok else LifecycleStage.FAILED)
        rec.runtime = snap.to_dict()
        _persist(rec)
        return rec

    if target == LifecycleStage.DEPLOY:
        # Require prior VERIFY success evidence — no silent deploy of unverified apps
        verified = False
        for e in reversed(rec.evidence or []):
            ed = e.to_dict() if hasattr(e, "to_dict") else e
            if ed.get("stage") == "VERIFY" and ed.get("ok"):
                verified = True
                break
        if not verified and not params.get("force_unverified"):
            _append_evidence(
                rec,
                StageEvidence(
                    stage="DEPLOY",
                    ok=False,
                    summary="deploy blocked: VERIFY evidence required",
                    details={"reason": "MISSING_VERIFY_EVIDENCE"},
                ),
            )
            _set_stage(rec, LifecycleStage.FAILED)
            _persist(rec)
            return rec

        provider = (params.get("provider") or "").strip().lower()
        if not provider:
            _append_evidence(
                rec,
                StageEvidence(
                    stage="DEPLOY",
                    ok=False,
                    summary="deploy blocked: provider required",
                    details={"reason": "PROVIDER_REQUIRED"},
                ),
            )
            _set_stage(rec, LifecycleStage.FAILED)
            _persist(rec)
            return rec

        from execution.deploy import get_adapter
        from execution.durable_store import save_deployment, new_id

        adapter = get_adapter(provider)
        credentials = dict(params.get("credentials") or {})
        # Never invent success — adapter must return real status
        result = await adapter.deploy(
            project_path=project_id,
            meta={"user_id": user_id, "project_id": project_id, "lifecycle_id": rec.lifecycle_id},
            credentials=credentials,
        )
        from execution.durable_store import new_id as _nid
        dep_id = _nid("dep_")

        # Success only if DEPLOYED + evidence present
        ok = result.status == DeploymentStatus.DEPLOYED and bool(result.evidence or result.deployment_id or result.url)
        # Fail closed on auth required
        if result.error == "DEPLOYMENT_AUTH_REQUIRED" or result.status == DeploymentStatus.FAILED:
            ok = False

        dep_record = {
            "deployment_id": dep_id,
            "provider": provider,
            "status": result.status.value,
            "provider_deployment_id": result.deployment_id,
            "url": result.url,
            "error": result.error,
            "evidence": result.evidence,
            "ok": ok,
            "at": time.time(),
        }
        try:
            save_deployment(
                deployment_id=dep_id,
                user_id=user_id,
                project_id=project_id,
                status=result.status.value,
                provider=provider,
                url=result.url,
                error=result.error,
                evidence=result.evidence,
            )
        except Exception as e:
            logger.debug("save_deployment failed: %s", type(e).__name__)

        rec.deployment = dep_record
        _append_evidence(
            rec,
            StageEvidence(
                stage="DEPLOY",
                ok=ok,
                summary=(
                    f"deployed to {provider}" if ok else (result.error or f"deploy {result.status.value}")
                ),
                details={"deployment": dep_record, "result": result.to_dict()},
                artifact_refs=[result.url] if result.url else [],
            ),
        )
        _set_stage(rec, LifecycleStage.DEPLOY if ok else LifecycleStage.FAILED)
        _persist(rec)
        return rec

    if target == LifecycleStage.OBSERVE:
        snap = snapshot(user_id, project_id, probe=True)
        rec.runtime = snap.to_dict()
        _append_evidence(
            rec,
            StageEvidence(
                stage="OBSERVE",
                ok=True,
                summary=f"observed state={snap.state} health={snap.health}",
                details={"runtime": snap.to_dict(), "deployment": rec.deployment},
            ),
        )
        _set_stage(rec, LifecycleStage.OBSERVE)
        _persist(rec)
        return rec

    if target == LifecycleStage.MAINTAIN:
        _append_evidence(
            rec,
            StageEvidence(
                stage="MAINTAIN",
                ok=True,
                summary="maintain phase active",
                details={"note": "further changes return to DEVELOP"},
            ),
        )
        _set_stage(rec, LifecycleStage.MAINTAIN)
        _persist(rec)
        return rec

    _append_evidence(
        rec,
        StageEvidence(stage=str(stage), ok=False, summary="unknown stage"),
    )
    _set_stage(rec, LifecycleStage.FAILED)
    _persist(rec)
    return rec


async def run_pipeline(
    user_id: str,
    project_id: str,
    stages: Optional[list[str]] = None,
    *,
    params: Optional[dict] = None,
    stop_on_failure: bool = True,
) -> LifecycleRecord:
    """Run an ordered lifecycle pipeline (default through VERIFY, not deploy)."""
    params = dict(params or {})
    seq = stages or [
        "CREATE",
        "DEVELOP",
        "BUILD",
        "PREVIEW",
        "VERIFY",
    ]
    rec = None
    for st in seq:
        rec = await advance(user_id, project_id, st, params=params)
        if stop_on_failure and rec.stage == LifecycleStage.FAILED:
            break
        if rec.cancelled:
            break
    assert rec is not None
    return rec


def reset_lifecycle_store_for_tests() -> None:
    _RECORDS.clear()
    _BY_PROJECT.clear()
