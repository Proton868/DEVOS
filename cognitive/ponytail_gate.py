"""
Ponytail — mandatory final quality gate for agent-produced work.

Invariant:
  AGENT → PONYTAIL → ACCEPTED OUTPUT → NUHA
Never:
  AGENT → NUHA  (for unvalidated code-bearing artifacts)

Nuha must not treat agent "done" as accepted without this gate.
Non-code work uses an applicability path (not a fake source-code review).
"""
from __future__ import annotations

import ast
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("devos.ponytail_gate")

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".css", ".scss", ".json", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".sh",
}
DOC_EXTENSIONS = {".md", ".txt", ".rst", ".adoc"}
CONFIG_EXTENSIONS = {".yml", ".yaml", ".toml", ".ini", ".env.example"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ext(path: str) -> str:
    p = (path or "").lower()
    if "." not in p.rsplit("/", 1)[-1]:
        return ""
    return "." + p.rsplit(".", 1)[-1]


def classify_artifact_kind(paths: list[str]) -> str:
    """code | document | config | mixed | empty"""
    if not paths:
        return "empty"
    kinds = set()
    for p in paths:
        e = _ext(p)
        if e in CODE_EXTENSIONS:
            kinds.add("code")
        elif e in DOC_EXTENSIONS:
            kinds.add("document")
        elif e in CONFIG_EXTENSIONS:
            kinds.add("config")
        else:
            kinds.add("other")
    if kinds == {"document"}:
        return "document"
    if kinds == {"config"}:
        return "config"
    if "code" in kinds:
        return "code" if kinds == {"code"} else "mixed"
    return "other"


@dataclass
class PonytailGateResult:
    check_id: str
    passed: bool
    status: str  # passed | failed | skipped | not_applicable | error
    summary: str = ""
    failures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    files_checked: list = field(default_factory=list)
    agent_id: Optional[str] = None
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    stage_results: list = field(default_factory=list)
    self_check: Optional[str] = None
    evidence_id: Optional[str] = None  # durable ponytail_checks row
    errors: list = field(default_factory=list)
    applicable: bool = True
    artifact_kind: str = "code"
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "status": self.status,
            "summary": self.summary,
            "failures": self.failures,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "files_checked": self.files_checked,
            "agent_id": self.agent_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "stage_results": self.stage_results,
            "self_check": self.self_check,
            "evidence_id": self.evidence_id,
            "errors": self.errors,
            "applicable": self.applicable,
            "artifact_kind": self.artifact_kind,
            "timestamp": self.timestamp,
        }


def _paths_from_changes(files_changed: list) -> list[str]:
    out = []
    for f in files_changed or []:
        if isinstance(f, dict):
            p = f.get("path") or f.get("file") or ""
        else:
            p = str(f)
        if p and p not in out:
            out.append(p)
    return out


def _check_python_syntax(path: str, content: str) -> list[str]:
    fails = []
    try:
        ast.parse(content)
    except SyntaxError as e:
        fails.append(f"{path}: Python syntax error: {e.msg} (line {e.lineno})")
    # Intentional broken markers used in tests / red flags
    if "PONYTAIL_FAIL" in content or "INTENTIONAL_BREAK" in content:
        fails.append(f"{path}: intentional break marker (PONYTAIL_FAIL/INTENTIONAL_BREAK)")
    if re.search(r"\braise\s+RuntimeError\s*\(\s*['\"]broken['\"]", content):
        fails.append(f"{path}: explicit broken RuntimeError")
    return fails


def _check_html(path: str, content: str) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    low = content.lower()
    if "PONYTAIL_FAIL" in content or "INTENTIONAL_BREAK" in content:
        fails.append(f"{path}: intentional break marker")
    if "<html" not in low and "<!doctype" not in low and len(content) > 20:
        # fragment pages ok with warning
        warns.append(f"{path}: HTML fragment without doctype/html root")
    if "<script" in low and "eval(" in low:
        fails.append(f"{path}: unsafe eval() in script")
    if re.search(r"onerror\s*=\s*['\"][^'\"]*alert", low):
        fails.append(f"{path}: suspicious inline onerror handler")
    # unclosed obvious tags
    if content.count("<html") != content.count("</html>") and "<html" in low:
        warns.append(f"{path}: mismatched html tags")
    return fails, warns


def _check_js(path: str, content: str) -> list[str]:
    fails = []
    if "PONYTAIL_FAIL" in content or "INTENTIONAL_BREAK" in content:
        fails.append(f"{path}: intentional break marker")
    if "eval(" in content:
        fails.append(f"{path}: use of eval()")
    # crude brace balance
    if content.count("{") != content.count("}"):
        fails.append(f"{path}: unbalanced braces")
    return fails


def _check_css(path: str, content: str) -> list[str]:
    fails = []
    if "PONYTAIL_FAIL" in content:
        fails.append(f"{path}: intentional break marker")
    if content.count("{") != content.count("}"):
        fails.append(f"{path}: unbalanced CSS braces")
    return fails


def _check_markdown(path: str, content: str) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    if not content.strip():
        fails.append(f"{path}: empty document")
    if "PONYTAIL_FAIL" in content:
        fails.append(f"{path}: intentional break marker")
    if len(content.strip()) < 20:
        warns.append(f"{path}: very short document")
    return fails, warns


async def validate_agent_artifacts(
    *,
    user_id: str,
    project_id: str = "default",
    goal: str = "",
    files_changed: Optional[list] = None,
    agent_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    task_id: Optional[str] = None,
    requirements: Optional[str] = None,
    force_code_gate: bool = False,
) -> PonytailGateResult:
    """
    Mandatory gate. PASS → accepted. FAIL → Nuha must re-delegate; never accept.
    """
    check_id = str(uuid.uuid4())
    files_changed = list(files_changed or [])
    paths = _paths_from_changes(files_changed)
    failures: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    stage_results: list[dict] = []
    present: list[str] = []
    missing: list[str] = []
    contents: dict[str, str] = {}

    # --- Stage: presence ---
    try:
        from execution.files import FileService

        fs = FileService(user_id, project_id or "default")
        candidates = list(paths)
        if not candidates and force_code_gate:
            candidates = ["index.html", "style.css", "script.js"]
        for path in candidates:
            if not path:
                continue
            try:
                data = fs.read(path)
                body = data.get("content") if isinstance(data, dict) else str(data)
                contents[path] = body or ""
                present.append(path)
            except Exception:
                missing.append(path)
    except Exception as e:
        errors.append(f"fileservice:{e}")

    if paths and not present:
        failures.append("no_artifacts_on_disk")
    if not paths and not present:
        failures.append("empty_delivery")

    stage_results.append({
        "stage": "structural",
        "status": "failed" if failures else "passed",
        "present": present,
        "missing": missing,
    })

    kind = classify_artifact_kind(present or paths)
    applicable = True

    if kind == "empty" or failures:
        result = PonytailGateResult(
            check_id=check_id,
            passed=False,
            status="failed",
            summary="Ponytail rejected: missing or empty artifacts",
            failures=failures + [f"missing:{m}" for m in missing],
            warnings=warnings,
            evidence={"present": present, "missing": missing},
            files_checked=present,
            agent_id=agent_id,
            mission_id=mission_id,
            task_id=task_id,
            stage_results=stage_results,
            errors=errors,
            applicable=True,
            artifact_kind=kind,
        )
        await _persist_check(result, user_id)
        return result

    # --- Stage: kind-specific checks ---
    if kind in ("code", "mixed") or force_code_gate:
        for path, content in contents.items():
            e = _ext(path)
            if e == ".py":
                failures.extend(_check_python_syntax(path, content))
            elif e in (".html", ".htm"):
                f, w = _check_html(path, content)
                failures.extend(f)
                warnings.extend(w)
            elif e in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
                failures.extend(_check_js(path, content))
            elif e in (".css", ".scss"):
                failures.extend(_check_css(path, content))
            elif e == ".json":
                import json
                try:
                    json.loads(content)
                except Exception as je:
                    failures.append(f"{path}: invalid JSON ({je})")
        stage_results.append({
            "stage": "code_quality",
            "status": "failed" if failures else "passed",
            "checks": ["syntax", "safety_markers", "structure"],
        })
        # requirements keyword coverage (light)
        req = (requirements or goal or "").lower()
        if req and present:
            # if goal mentions website/html, require an html file among present
            if any(k in req for k in ("website", "html", "landing", "page")):
                if not any(p.endswith((".html", ".htm")) for p in present):
                    failures.append("requirements: expected HTML artifact for website goal")
        stage_results.append({
            "stage": "requirements",
            "status": "failed" if any("requirements:" in f for f in failures) else "passed",
        })

    elif kind == "document":
        for path, content in contents.items():
            f, w = _check_markdown(path, content)
            failures.extend(f)
            warnings.extend(w)
        stage_results.append({
            "stage": "document_quality",
            "status": "failed" if failures else "passed",
        })
        applicable = True  # document gate still applies (non-code path)

    elif kind == "config":
        for path, content in contents.items():
            if not content.strip():
                failures.append(f"{path}: empty config")
            if "PONYTAIL_FAIL" in content:
                failures.append(f"{path}: intentional break marker")
        stage_results.append({
            "stage": "config_quality",
            "status": "failed" if failures else "passed",
        })

    else:
        # other: presence-only acceptance with warning
        warnings.append(f"artifact_kind={kind}: limited Ponytail checks applied")
        stage_results.append({"stage": "generic", "status": "passed" if present else "failed"})

    # Optional full LLM pipeline
    if os.environ.get("DEVOS_PONYTAIL_FULL") == "1" and kind in ("code", "mixed"):
        try:
            from cognitive.ponytail import PonytailPipeline

            ctx = "\n\n".join(f"### {p}\n{contents[p][:4000]}" for p in present[:5])
            run = await PonytailPipeline().run(goal or "validate agent output", ctx)
            stage_results.append({"stage": "pipeline", "status": run.final_status})
            if run.final_status not in ("done", "passed", "success"):
                failures.append(f"pipeline:{run.final_status}")
        except Exception as e:
            warnings.append(f"pipeline_skipped:{e}")

    passed = not failures and not errors
    status = "passed" if passed else "failed"
    summary = (
        f"Ponytail {status}: {len(present)} file(s) checked"
        + (f"; {len(failures)} failure(s)" if failures else "")
    )

    result = PonytailGateResult(
        check_id=check_id,
        passed=passed,
        status=status,
        summary=summary,
        failures=failures,
        warnings=warnings,
        evidence={
            "present": present,
            "missing": missing,
            "goal": (goal or "")[:300],
            "requirements": (requirements or "")[:300],
        },
        files_checked=present,
        agent_id=agent_id,
        mission_id=mission_id,
        task_id=task_id,
        stage_results=stage_results,
        self_check="ponytail_gate_v2",
        errors=errors,
        applicable=applicable,
        artifact_kind=kind,
    )
    await _persist_check(result, user_id)
    return result


async def _persist_check(result: PonytailGateResult, user_id: str) -> None:
    try:
        from core.repositories.agency import record_ponytail_check

        eid = await record_ponytail_check(
            user_id=user_id,
            status=result.status,
            agent_id=result.agent_id,
            mission_id=result.mission_id,
            task_id=result.task_id,
            stage_results=result.stage_results,
            self_check=result.self_check,
            summary=result.summary + ("; " + "; ".join(result.failures[:5]) if result.failures else ""),
        )
        result.evidence_id = eid
    except Exception as e:
        logger.debug("ponytail persist: %s", e)


def assert_accepted(gate: PonytailGateResult) -> None:
    """Raise if caller attempts to accept without PASS."""
    if not gate.passed or gate.status != "passed":
        raise PermissionError(
            f"Ponytail has not accepted this output (status={gate.status}, "
            f"failures={gate.failures}). Nuha must not treat agent completion as accepted."
        )
