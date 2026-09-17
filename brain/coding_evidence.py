"""
Structured coding-mission evidence.

Evidence must reference real execution — never fabricate success.
Failed missions still retain useful failure evidence.
Respects authorization: only owner/mission-scoped refs; no secret payloads.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


REQUIRED_CODING_FIELDS = (
    "mission_id",
    "project_id",
    "agent_id",
    "files_changed",
    "commands",
    "validation",
    "timestamps",
)


@dataclass
class CommandEvidence:
    command: str
    exit_code: int
    ok: bool
    stdout_tail: str = ""
    stderr_tail: str = ""
    kind: str = "command"  # test | build | lint | typecheck | command
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "stdout_tail": (self.stdout_tail or "")[-2000:],
            "stderr_tail": (self.stderr_tail or "")[-2000:],
            "kind": self.kind,
            "at": self.at,
        }


@dataclass
class CodingEvidence:
    """Complete evidence packet for a coding mission."""

    evidence_id: str
    mission_id: str
    project_id: str
    workspace_id: str = "default"
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    agent_id: Optional[str] = None
    persona_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    tests_executed: list[dict] = field(default_factory=list)
    validation: Optional[dict] = None
    artifacts: list[str] = field(default_factory=list)
    acceptance_decision: Optional[dict] = None
    success: bool = False
    error: Optional[str] = None
    fabricated: bool = False  # always False when built via builders
    timestamps: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "mission_id": self.mission_id,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "persona_id": self.persona_id,
            "provider": self.provider,
            "model": self.model,
            "files_changed": list(self.files_changed or []),
            "commands": list(self.commands or []),
            "tests_executed": list(self.tests_executed or []),
            "validation": self.validation,
            "artifacts": list(self.artifacts or []),
            "acceptance_decision": self.acceptance_decision,
            "success": self.success,
            "error": self.error,
            "fabricated": self.fabricated,
            "timestamps": dict(self.timestamps or {}),
            "meta": dict(self.meta or {}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CodingEvidence":
        return cls(
            evidence_id=str(d.get("evidence_id") or ""),
            mission_id=str(d.get("mission_id") or ""),
            project_id=str(d.get("project_id") or ""),
            workspace_id=str(d.get("workspace_id") or "default"),
            user_id=d.get("user_id"),
            tenant_id=d.get("tenant_id"),
            agent_id=d.get("agent_id"),
            persona_id=d.get("persona_id"),
            provider=d.get("provider"),
            model=d.get("model"),
            files_changed=list(d.get("files_changed") or []),
            commands=list(d.get("commands") or []),
            tests_executed=list(d.get("tests_executed") or []),
            validation=d.get("validation"),
            artifacts=list(d.get("artifacts") or []),
            acceptance_decision=d.get("acceptance_decision"),
            success=bool(d.get("success")),
            error=d.get("error"),
            fabricated=bool(d.get("fabricated")),
            timestamps=dict(d.get("timestamps") or {}),
            meta=dict(d.get("meta") or {}),
        )


def _scrub(text: str, limit: int = 2000) -> str:
    """Strip obvious secret-shaped content from command output tails."""
    try:
        from core.redaction import scrub_secrets
        return scrub_secrets((text or "")[:limit])
    except Exception:
        t = text or ""
        for pat in ("sk-", "ghp_", "Bearer ", "SUPABASE_"):
            if pat in t:
                t = t.replace(pat, "[REDACTED]")
        return t[:limit]


def build_coding_evidence(
    *,
    mission_id: str,
    project_id: str,
    workspace_id: str = "default",
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    files_changed: Optional[list] = None,
    commands: Optional[list] = None,
    tests_executed: Optional[list] = None,
    validation: Optional[dict] = None,
    artifacts: Optional[list] = None,
    success: bool = False,
    error: Optional[str] = None,
    acceptance_decision: Optional[dict] = None,
) -> CodingEvidence:
    """Build evidence from real execution inputs. Does not invent commands/files."""
    now = datetime.now(timezone.utc).isoformat()
    files: list[str] = []
    for f in files_changed or []:
        if isinstance(f, dict):
            p = f.get("path") or f.get("file")
            if p:
                files.append(str(p))
        elif isinstance(f, str) and f.strip():
            files.append(f.strip())

    cmd_rows: list[dict] = []
    for c in commands or []:
        if not isinstance(c, dict):
            continue
        cmd = str(c.get("command") or "").strip()
        if not cmd:
            continue
        code = int(c.get("exit_code") if c.get("exit_code") is not None else (0 if c.get("ok") else 1))
        cmd_rows.append(CommandEvidence(
            command=cmd,
            exit_code=code,
            ok=bool(c.get("ok")) and code == 0,
            stdout_tail=_scrub(str(c.get("stdout") or c.get("stdout_tail") or "")),
            stderr_tail=_scrub(str(c.get("stderr") or c.get("stderr_tail") or "")),
            kind=str(c.get("kind") or "command"),
            at=str(c.get("at") or now),
        ).to_dict())

    tests: list[dict] = []
    for t in tests_executed or []:
        if isinstance(t, dict) and (t.get("command") or t.get("name")):
            tests.append({
                "name": t.get("name") or t.get("command"),
                "command": t.get("command"),
                "ok": bool(t.get("ok")),
                "exit_code": t.get("exit_code"),
                "at": t.get("at") or now,
            })

    arts = [str(a) for a in (artifacts or []) if a]
    if not arts and files:
        arts = [f"file:{p}" for p in files]

    eid = "cev_" + hashlib.sha256(
        f"{mission_id}:{project_id}:{now}:{uuid.uuid4().hex[:8]}".encode()
    ).hexdigest()[:24]

    return CodingEvidence(
        evidence_id=eid,
        mission_id=str(mission_id or ""),
        project_id=str(project_id or ""),
        workspace_id=str(workspace_id or "default"),
        user_id=user_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        persona_id=persona_id,
        provider=provider,
        model=model,
        files_changed=files,
        commands=cmd_rows,
        tests_executed=tests,
        validation=dict(validation) if validation else None,
        artifacts=arts,
        acceptance_decision=acceptance_decision,
        success=bool(success),
        error=(error or None),
        fabricated=False,
        timestamps={"created_at": now, "updated_at": now},
    )


def validate_coding_evidence(
    evidence: CodingEvidence | dict,
    *,
    require_commands: bool = True,
    require_files_if_success: bool = True,
    expected_mission_id: Optional[str] = None,
    expected_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Completeness + anti-fabrication checks.

    Returns {ok, reason, checks}.
    Incomplete or fabricated packets cannot satisfy coding acceptance.
    """
    ev = evidence if isinstance(evidence, CodingEvidence) else CodingEvidence.from_dict(evidence or {})
    checks = {
        "has_evidence_id": bool(str(ev.evidence_id or "").strip()),
        "has_mission_id": bool(str(ev.mission_id or "").strip()),
        "has_project_id": bool(str(ev.project_id or "").strip()),
        "has_agent": bool(str(ev.agent_id or ev.persona_id or "").strip()),
        "has_files": bool(ev.files_changed),
        "has_commands": bool(ev.commands),
        "commands_have_exit_codes": True,
        "not_fabricated": not bool(ev.fabricated),
        "mission_match": True,
        "owner_match": True,
        "has_timestamps": bool(ev.timestamps),
        "has_validation": ev.validation is not None,
    }

    for c in ev.commands or []:
        if not isinstance(c, dict):
            checks["commands_have_exit_codes"] = False
            break
        if "exit_code" not in c:
            checks["commands_have_exit_codes"] = False
            break

    if expected_mission_id and ev.mission_id and expected_mission_id != ev.mission_id:
        checks["mission_match"] = False
    if expected_user_id and ev.user_id and expected_user_id != ev.user_id:
        checks["owner_match"] = False

    if not checks["not_fabricated"]:
        return {"ok": False, "reason": "fabricated_evidence", "checks": checks}
    if not checks["has_evidence_id"]:
        return {"ok": False, "reason": "evidence_id_missing", "checks": checks}
    if not checks["has_mission_id"]:
        return {"ok": False, "reason": "mission_id_missing", "checks": checks}
    if not checks["has_project_id"]:
        return {"ok": False, "reason": "project_id_missing", "checks": checks}
    if not checks["mission_match"]:
        return {"ok": False, "reason": "mission_mismatch", "checks": checks}
    if not checks["owner_match"]:
        return {"ok": False, "reason": "owner_mismatch", "checks": checks}
    if not checks["has_timestamps"]:
        return {"ok": False, "reason": "timestamps_missing", "checks": checks}
    if not checks["has_agent"]:
        return {"ok": False, "reason": "agent_missing", "checks": checks}

    if require_commands and not checks["has_commands"]:
        # Failure evidence may still be useful with error-only, but acceptance needs commands
        return {"ok": False, "reason": "commands_missing", "checks": checks}
    if not checks["commands_have_exit_codes"] and checks["has_commands"]:
        return {"ok": False, "reason": "exit_codes_missing", "checks": checks}

    if ev.success and require_files_if_success and not checks["has_files"]:
        return {"ok": False, "reason": "success_without_files", "checks": checks}
    if ev.success and not checks["has_validation"]:
        return {"ok": False, "reason": "success_without_validation", "checks": checks}

    return {"ok": True, "reason": "coding_evidence_complete", "checks": checks}


def failure_evidence(
    *,
    mission_id: str,
    project_id: str,
    error: str,
    agent_id: Optional[str] = None,
    commands: Optional[list] = None,
    files_changed: Optional[list] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    user_id: Optional[str] = None,
) -> CodingEvidence:
    """Retain useful failure evidence without claiming success."""
    return build_coding_evidence(
        mission_id=mission_id,
        project_id=project_id,
        agent_id=agent_id or "unknown",
        user_id=user_id,
        provider=provider,
        model=model,
        files_changed=files_changed or [],
        commands=commands or [],
        validation={"ok": False, "error": error},
        success=False,
        error=error,
    )


def attach_acceptance(ev: CodingEvidence, decision: dict) -> CodingEvidence:
    ev.acceptance_decision = dict(decision or {})
    ev.timestamps["accepted_at"] = datetime.now(timezone.utc).isoformat()
    ev.timestamps["updated_at"] = ev.timestamps["accepted_at"]
    return ev


def persist_coding_evidence(ev: CodingEvidence) -> str:
    """Best-effort durable record via EvidenceChainManager; returns evidence_id."""
    try:
        from governance.evidence import EvidenceNode, EvidenceChainManager
        mgr = EvidenceChainManager()
        chain = mgr.get_or_create_chain(
            chain_id=f"coding-{ev.mission_id}",
            label="coding_mission",
        )
        node = EvidenceNode(
            node_id=ev.evidence_id,
            chain_id=getattr(chain, "chain_id", f"coding-{ev.mission_id}"),
            action="coding.evidence",
            actor_id=ev.agent_id or "agent",
            status="success" if ev.success else "failed",
            metadata={
                "mission_id": ev.mission_id,
                "project_id": ev.project_id,
                "user_id": ev.user_id,
                "tenant_id": ev.tenant_id,
                "files_changed": ev.files_changed[:50],
                "command_count": len(ev.commands or []),
                "fabricated": False,
            },
        )
        if hasattr(chain, "add_node"):
            chain.add_node(node)
        if hasattr(mgr, "save_chain"):
            mgr.save_chain(chain)
    except Exception:
        pass
    return ev.evidence_id
