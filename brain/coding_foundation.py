"""Universal coding-agent foundation for DevOS.

Single architecture for arbitrary software projects — not language-specific agents.

Inspection uses workspace files only (FileService). Commands/tests/builds run only
through governed AgentRuntime tools (UCIP + allowlists). Validation distinguishes
file writes from command/test/build/runtime success. Never enables fake runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ProjectEcosystem(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    NODE = "node"
    VITE = "vite"
    NEXTJS = "nextjs"
    ANGULAR = "angular"
    REACT = "react"
    HTML_STATIC = "html_static"
    SHELL_BUILD = "shell_build"
    UNKNOWN = "unknown"


class ValidationKind(str, Enum):
    """What was proven — file write is never equivalent to tests/build."""
    FILES_WRITTEN = "files_written"
    COMMAND_OK = "command_ok"
    TESTS_OK = "tests_ok"
    BUILD_OK = "build_ok"
    RUNTIME_OK = "runtime_ok"


@dataclass
class WorkspaceInspection:
    ecosystem: ProjectEcosystem
    kind: str
    framework: Optional[str]
    package_manager: Optional[str]
    confidence: float
    config_files: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    suggested_test_commands: list[str] = field(default_factory=list)
    suggested_build_commands: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blank_project: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem.value,
            "kind": self.kind,
            "framework": self.framework,
            "package_manager": self.package_manager,
            "confidence": self.confidence,
            "config_files": list(self.config_files),
            "scripts": dict(self.scripts),
            "suggested_test_commands": list(self.suggested_test_commands),
            "suggested_build_commands": list(self.suggested_build_commands),
            "notes": list(self.notes),
            "blank_project": self.blank_project,
            "never_assume_blank": True,
        }


def _map_kind_to_ecosystem(kind: str, framework: Optional[str]) -> ProjectEcosystem:
    k = (kind or "").upper()
    f = (framework or "").lower()
    if k == "NEXTJS_APP" or f == "nextjs":
        return ProjectEcosystem.NEXTJS
    if k == "VITE_APP" or f == "vite":
        return ProjectEcosystem.VITE
    if k == "ANGULAR_APP" or f == "angular":
        return ProjectEcosystem.ANGULAR
    if k == "REACT_APP" or f == "react":
        return ProjectEcosystem.REACT
    if k == "TYPESCRIPT_APP" or f == "typescript":
        return ProjectEcosystem.TYPESCRIPT
    if k == "NODE_APP" or f == "node":
        return ProjectEcosystem.NODE
    if k == "PYTHON_APP" or f == "python":
        return ProjectEcosystem.PYTHON
    if k == "STATIC_SITE" or f == "static":
        return ProjectEcosystem.HTML_STATIC
    if k == "SHELL_BUILD" or f == "shell":
        return ProjectEcosystem.SHELL_BUILD
    if f == "javascript":
        return ProjectEcosystem.JAVASCRIPT
    return ProjectEcosystem.UNKNOWN


def _suggest_commands(ecosystem: ProjectEcosystem, scripts: dict[str, str], pm: Optional[str]) -> tuple[list[str], list[str]]:
    tests: list[str] = []
    builds: list[str] = []
    run = "npm"
    if pm == "pnpm":
        run = "pnpm"
    elif pm == "yarn":
        run = "yarn"
    elif pm == "bun":
        run = "bun"

    if "test" in scripts:
        tests.append(f"{run} test")
    if "build" in scripts:
        builds.append(f"{run} run build")

    if ecosystem == ProjectEcosystem.PYTHON:
        if not tests:
            tests.extend(["pytest -q", "python -m pytest -q"])
        if not builds:
            builds.append("python -m compileall .")
    elif ecosystem in (
        ProjectEcosystem.NEXTJS,
        ProjectEcosystem.VITE,
        ProjectEcosystem.ANGULAR,
        ProjectEcosystem.REACT,
        ProjectEcosystem.NODE,
        ProjectEcosystem.TYPESCRIPT,
        ProjectEcosystem.JAVASCRIPT,
    ):
        if not tests:
            tests.append(f"{run} test")
        if not builds:
            builds.append(f"{run} run build")
    elif ecosystem == ProjectEcosystem.SHELL_BUILD:
        builds.append("make")
        builds.append("bash build.sh")
    elif ecosystem == ProjectEcosystem.HTML_STATIC:
        # No build required; runtime check is open/serve — agent must not claim build success
        pass
    return tests, builds


def inspect_workspace(fs) -> WorkspaceInspection:
    """Detect project type from files. Never assumes a blank project."""
    from execution.app_detect import detect_application

    detected = detect_application(fs)
    kind = detected.get("kind") or "UNKNOWN_APP"
    framework = detected.get("framework")
    ecosystem = _map_kind_to_ecosystem(kind, framework)
    scripts = dict(detected.get("scripts") or {})
    pm = detected.get("package_manager")

    config_files: list[str] = []
    candidates = [
        "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json",
        "tsconfig.json", "jsconfig.json", "vite.config.js", "vite.config.ts",
        "vite.config.mjs", "next.config.js", "next.config.mjs", "next.config.ts",
        "angular.json", "pyproject.toml", "requirements.txt", "setup.py",
        "Pipfile", "poetry.lock", "Makefile", "makefile", "build.sh",
        "index.html", "Cargo.toml", "go.mod",
    ]
    for rel in candidates:
        try:
            if hasattr(fs, "_resolve"):
                if fs._resolve(rel).exists():
                    config_files.append(rel)
            elif hasattr(fs, "exists") and fs.exists(rel):
                config_files.append(rel)
        except Exception:
            continue

    blank = len(config_files) == 0 and kind in ("UNKNOWN_APP", "")
    notes: list[str] = []
    if blank:
        notes.append("no_config_detected_still_inspect_before_write")
    else:
        notes.append("existing_project_read_before_modify")

    tests, builds = _suggest_commands(ecosystem, scripts, pm)
    return WorkspaceInspection(
        ecosystem=ecosystem,
        kind=kind,
        framework=framework,
        package_manager=pm,
        confidence=float(detected.get("confidence") or 0.0),
        config_files=config_files,
        scripts=scripts,
        suggested_test_commands=tests,
        suggested_build_commands=builds,
        notes=notes,
        blank_project=blank,
    )


@dataclass
class CommandOutcome:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: Optional[int] = None
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_tail": (self.stdout or "")[-2000:],
            "stderr_tail": (self.stderr or "")[-2000:],
            "duration_ms": self.duration_ms,
            "ok": self.ok and self.exit_code == 0,
        }


def normalize_command_result(
    *,
    command: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    duration_ms: Optional[int] = None,
) -> CommandOutcome:
    return CommandOutcome(
        command=command,
        exit_code=int(exit_code),
        stdout=stdout or "",
        stderr=stderr or "",
        duration_ms=duration_ms,
        ok=(int(exit_code) == 0),
    )


def evaluate_coding_validation(
    *,
    files_changed: Optional[list] = None,
    command_results: Optional[list[dict]] = None,
    tests_passed: Optional[bool] = None,
    build_passed: Optional[bool] = None,
    runtime_passed: Optional[bool] = None,
    require_tests: bool = False,
    require_build: bool = False,
) -> dict[str, Any]:
    """Truthful coding outcome — files alone never imply tests/build success."""
    files_changed = list(files_changed or [])
    command_results = list(command_results or [])
    has_files = bool(files_changed)
    cmds_ok = all(bool(c.get("ok")) for c in command_results) if command_results else None

    proven: list[str] = []
    if has_files:
        proven.append(ValidationKind.FILES_WRITTEN.value)
    if cmds_ok is True:
        proven.append(ValidationKind.COMMAND_OK.value)
    if tests_passed is True:
        proven.append(ValidationKind.TESTS_OK.value)
    if build_passed is True:
        proven.append(ValidationKind.BUILD_OK.value)
    if runtime_passed is True:
        proven.append(ValidationKind.RUNTIME_OK.value)

    reasons: list[str] = []
    ok = True
    if require_tests and tests_passed is not True:
        ok = False
        reasons.append("tests_not_proven")
    if require_build and build_passed is not True:
        ok = False
        reasons.append("build_not_proven")
    if not has_files and not command_results and tests_passed is None and build_passed is None:
        ok = False
        reasons.append("no_evidence")

    # Explicit: files written is never full success when tests/build required
    if has_files and not reasons and not require_tests and not require_build:
        # Files-only task may be ok, but status is files_only
        synthesis = "files_only"
    elif ok and (tests_passed or build_passed or runtime_passed or cmds_ok):
        synthesis = "validated"
    elif ok:
        synthesis = "partial"
    else:
        synthesis = "failed"

    return {
        "ok": ok and synthesis in ("validated", "files_only", "partial"),
        "synthesis": synthesis,
        "proven": proven,
        "reasons": reasons,
        "files_written": has_files,
        "tests_passed": tests_passed,
        "build_passed": build_passed,
        "runtime_passed": runtime_passed,
        "commands_ok": cmds_ok,
        # Hard rule for mission language
        "files_do_not_imply_tests_or_build": True,
    }


def coding_system_guidance(inspection: WorkspaceInspection) -> str:
    """Short prompt block for AgentRuntime — inspection before modify."""
    d = inspection.to_dict()
    return (
        "CODING WORKSPACE (authoritative inspection)\n"
        f"- ecosystem: {d['ecosystem']}\n"
        f"- kind: {d['kind']} framework={d['framework']}\n"
        f"- package_manager: {d['package_manager']}\n"
        f"- config_files: {', '.join(d['config_files']) or '(none detected)'}\n"
        f"- blank_project: {d['blank_project']}\n"
        "- RULE: read relevant files before modifying; do not assume blank project.\n"
        "- RULE: file creation ≠ tests passed ≠ build passed ≠ runtime ok.\n"
        f"- suggested_tests: {d['suggested_test_commands']}\n"
        f"- suggested_builds: {d['suggested_build_commands']}\n"
    )
