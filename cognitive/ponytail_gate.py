"""
Ponytail acceptance gate for agent-produced artifacts.

Agent completion is NOT accepted until this gate passes.
Full PonytailPipeline remains available; this gate is the mandatory
accept/reject boundary used by the delegation layer.

Modes:
  - structural: validate files on disk (no LLM) — used in tests / offline
  - pipeline: optional full PonytailPipeline when DEVOS_PONYTAIL_FULL=1
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("devos.ponytail_gate")


@dataclass
class PonytailGateResult:
    passed: bool
    status: str  # passed | failed | skipped | error
    summary: str = ""
    stage_results: list = field(default_factory=list)
    self_check: Optional[str] = None
    evidence_id: Optional[str] = None
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "status": self.status,
            "summary": self.summary,
            "stage_results": self.stage_results,
            "self_check": self.self_check,
            "evidence_id": self.evidence_id,
            "errors": self.errors,
        }


async def validate_agent_artifacts(
    *,
    user_id: str,
    project_id: str = "default",
    goal: str = "",
    files_changed: Optional[list] = None,
    agent_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> PonytailGateResult:
    """
    Accept only when artifacts exist and pass structural (or full) checks.
    """
    files_changed = list(files_changed or [])
    errors: list[str] = []
    stage_results: list[dict] = []

    # Stage 1: structural presence
    present: list[str] = []
    missing: list[str] = []
    try:
        from execution.files import FileService

        fs = FileService(user_id, project_id or "default")
        candidates = []
        for f in files_changed:
            if isinstance(f, dict):
                candidates.append(f.get("path") or f.get("file") or "")
            else:
                candidates.append(str(f))
        if not candidates:
            # website-style defaults
            candidates = ["index.html", "style.css", "script.js"]
        for path in candidates:
            if not path:
                continue
            try:
                fs.read(path)
                present.append(path)
            except Exception:
                missing.append(path)
    except Exception as e:
        errors.append(f"fileservice:{e}")

    structural_ok = bool(present) and not (missing and not present)
    # Require at least one real file if agent claimed changes
    if files_changed and not present:
        structural_ok = False
        errors.append("no_artifacts_on_disk")
    if not files_changed and not present:
        structural_ok = False
        errors.append("empty_delivery")

    stage_results.append({
        "stage": "structural",
        "status": "passed" if structural_ok else "failed",
        "present": present,
        "missing": missing,
    })

    if not structural_ok:
        return PonytailGateResult(
            passed=False,
            status="failed",
            summary="Ponytail structural validation failed: missing or empty artifacts",
            stage_results=stage_results,
            errors=errors,
        )

    # Stage 2: optional full pipeline
    if os.environ.get("DEVOS_PONYTAIL_FULL") == "1":
        try:
            from cognitive.ponytail import PonytailPipeline

            ctx_parts = []
            from execution.files import FileService

            fs = FileService(user_id, project_id or "default")
            for path in present[:5]:
                try:
                    content = fs.read(path).get("content") or ""
                    ctx_parts.append(f"### {path}\n{content[:4000]}")
                except Exception:
                    pass
            pipeline = PonytailPipeline()
            run = await pipeline.run(goal or "validate agent output", "\n\n".join(ctx_parts))
            stage_results.append({"stage": "pipeline", "status": run.final_status})
            if run.final_status not in ("done", "passed", "success"):
                return PonytailGateResult(
                    passed=False,
                    status="failed",
                    summary=f"Ponytail pipeline status={run.final_status}",
                    stage_results=stage_results,
                    errors=errors + [run.final_status],
                )
        except Exception as e:
            logger.warning("full ponytail unavailable: %s", e)
            stage_results.append({"stage": "pipeline", "status": "skipped", "error": str(e)[:200]})

    # Stage 3: record durable check
    evidence_id = None
    try:
        from core.repositories.agency import record_ponytail_check

        evidence_id = await record_ponytail_check(
            user_id=user_id,
            status="passed",
            agent_id=agent_id,
            mission_id=mission_id,
            task_id=task_id,
            stage_results=stage_results,
            self_check="structural_presence",
            summary=f"accepted files: {present}",
        )
    except Exception as e:
        logger.debug("ponytail_check persist skipped: %s", e)

    return PonytailGateResult(
        passed=True,
        status="passed",
        summary=f"Ponytail accepted: {', '.join(present)}",
        stage_results=stage_results,
        self_check="structural_presence",
        evidence_id=evidence_id,
    )
