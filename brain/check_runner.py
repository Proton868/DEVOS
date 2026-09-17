"""
Structured build / test / lint / typecheck for the coding agent.

Generic CheckCommand abstraction + project adapters (no hard-coded paths).

  detect commands → run → parse failures → likely files → edit hook → rerun
  until success or bounded exhaustion.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from brain.coding_foundation import ProjectEcosystem, inspect_workspace

CommandRunner = Callable[[str, dict], Awaitable[dict]]
# returns {ok, exit_code, stdout, stderr, command}
EditHook = Callable[[list[str], "CheckFailure"], Awaitable[dict]]
# returns {ok, files_changed: [...]}


class CheckKind(str, Enum):
    TEST = "test"
    BUILD = "build"
    LINT = "lint"
    TYPECHECK = "typecheck"


@dataclass
class CheckCommand:
    kind: CheckKind
    command: str
    adapter: str  # python_pytest | node_npm | typescript | vite | nextjs | angular | generic
    cwd_hint: str = "."  # relative, never absolute project path hard-codes
    timeout_s: int = 180

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "command": self.command,
            "adapter": self.adapter,
            "cwd_hint": self.cwd_hint,
            "timeout_s": self.timeout_s,
        }


@dataclass
class CheckFailure:
    summary: str
    likely_files: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    raw_tail: str = ""
    adapter: str = "generic"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    ok: bool
    command: str
    kind: str
    adapter: str
    exit_code: int = 1
    stdout: str = ""
    stderr: str = ""
    failure: Optional[CheckFailure] = None

    def to_dict(self) -> dict:
        d = {
            "ok": self.ok,
            "command": self.command,
            "kind": self.kind,
            "adapter": self.adapter,
            "exit_code": self.exit_code,
            "stdout_tail": (self.stdout or "")[-2000:],
            "stderr_tail": (self.stderr or "")[-2000:],
            "failure": self.failure.to_dict() if self.failure else None,
        }
        return d


@dataclass
class DebugLoopResult:
    ok: bool
    attempts: int
    max_attempts: int
    final: Optional[dict] = None
    history: list[dict] = field(default_factory=list)
    exhausted: bool = False
    files_touched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── failure parsers ──────────────────────────────────────────────────────────

_FILE_RE = re.compile(
    r"(?:^|\s|/)([\w./-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|vue|css|scss|html))\b"
)
_PYTEST_FILE = re.compile(r"([\w./-]+\.py):\d+")
_TSC_FILE = re.compile(r"([\w./-]+\.tsx?)\(\d+,\d+\)")
_ESLINT_FILE = re.compile(r"([\w./-]+\.(?:js|jsx|ts|tsx|mjs))")


def parse_failure(adapter: str, stdout: str, stderr: str, exit_code: int) -> CheckFailure:
    text = f"{stdout or ''}\n{stderr or ''}"
    tail = text[-3000:]
    files: list[str] = []
    codes: list[str] = []
    summary = f"exit_code={exit_code}"

    if adapter in ("python_pytest", "python"):
        files.extend(m.group(1) for m in _PYTEST_FILE.finditer(text))
        if "FAILED" in text:
            summary = "pytest failures"
        for m in re.finditer(r"E\s+(\w+Error:.*)", text):
            codes.append(m.group(1)[:120])
        if "ModuleNotFoundError" in text:
            codes.append("ModuleNotFoundError")
    elif adapter in ("typescript", "tsc"):
        files.extend(m.group(1) for m in _TSC_FILE.finditer(text))
        codes.extend(re.findall(r"error TS\d+", text))
        summary = "TypeScript compiler errors" if codes else summary
    elif adapter in ("node_npm", "vite", "nextjs", "angular", "generic"):
        files.extend(m.group(1) for m in _FILE_RE.finditer(text))
        if "ERROR in" in text or "Failed to compile" in text:
            summary = "compile failure"
        if "ELIFECYCLE" in text:
            codes.append("ELIFECYCLE")
        codes.extend(re.findall(r"error TS\d+", text))

    # de-dupe preserve order
    seen = set()
    uniq = []
    for f in files:
        f = f.lstrip("./")
        if f.startswith("node_modules"):
            continue
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return CheckFailure(
        summary=summary[:300],
        likely_files=uniq[:20],
        error_codes=list(dict.fromkeys(codes))[:20],
        raw_tail=tail,
        adapter=adapter,
    )


# ── detection ────────────────────────────────────────────────────────────────

def _pm_run(pm: Optional[str]) -> str:
    if pm == "pnpm":
        return "pnpm"
    if pm == "yarn":
        return "yarn"
    if pm == "bun":
        return "bun"
    return "npm"


def detect_checks(fs) -> list[CheckCommand]:
    """Discover available test/build/lint/typecheck commands from workspace files."""
    insp = inspect_workspace(fs)
    scripts = dict(insp.scripts or {})
    pm = _pm_run(insp.package_manager)
    eco = insp.ecosystem
    cfg = set(insp.config_files or [])
    out: list[CheckCommand] = []

    def add(kind: CheckKind, cmd: str, adapter: str) -> None:
        if any(c.command == cmd and c.kind == kind for c in out):
            return
        out.append(CheckCommand(kind=kind, command=cmd, adapter=adapter))

    # Python
    if eco == ProjectEcosystem.PYTHON or any(
        x in cfg for x in ("pyproject.toml", "requirements.txt", "setup.py", "pytest.ini", "tox.ini")
    ):
        add(CheckKind.TEST, "python -m pytest -q", "python_pytest")
        add(CheckKind.BUILD, "python -m compileall -q .", "python")
        add(CheckKind.LINT, "python -m ruff check .", "python")
        add(CheckKind.TYPECHECK, "python -m mypy .", "python")

    # package.json script driven
    if "package.json" in cfg or eco in (
        ProjectEcosystem.NODE, ProjectEcosystem.VITE, ProjectEcosystem.NEXTJS,
        ProjectEcosystem.ANGULAR, ProjectEcosystem.REACT, ProjectEcosystem.TYPESCRIPT,
        ProjectEcosystem.JAVASCRIPT,
    ):
        adapter = "node_npm"
        if eco == ProjectEcosystem.VITE or any(x.startswith("vite.config") for x in cfg):
            adapter = "vite"
        elif eco == ProjectEcosystem.NEXTJS or any(x.startswith("next.config") for x in cfg):
            adapter = "nextjs"
        elif eco == ProjectEcosystem.ANGULAR or "angular.json" in cfg:
            adapter = "angular"
        elif "tsconfig.json" in cfg:
            adapter = "typescript"

        if "test" in scripts:
            add(CheckKind.TEST, f"{pm} test", adapter)
        elif adapter == "angular":
            add(CheckKind.TEST, f"{pm} test -- --watch=false --browsers=ChromeHeadless", "angular")
        else:
            add(CheckKind.TEST, f"{pm} test -- --watchAll=false", adapter)

        if "build" in scripts:
            add(CheckKind.BUILD, f"{pm} run build", adapter)
        elif adapter == "vite":
            add(CheckKind.BUILD, f"{pm} run build", "vite")
        elif adapter == "nextjs":
            add(CheckKind.BUILD, f"{pm} run build", "nextjs")

        if "lint" in scripts:
            add(CheckKind.LINT, f"{pm} run lint", adapter)
        elif adapter == "nextjs":
            add(CheckKind.LINT, f"{pm} run lint", "nextjs")

        if "typecheck" in scripts:
            add(CheckKind.TYPECHECK, f"{pm} run typecheck", "typescript")
        elif "tsconfig.json" in cfg:
            add(CheckKind.TYPECHECK, "npx tsc --noEmit", "typescript")

    # Fallbacks from inspection suggestions
    for cmd in insp.suggested_test_commands or []:
        add(CheckKind.TEST, cmd, out[0].adapter if out else "generic")
    for cmd in insp.suggested_build_commands or []:
        add(CheckKind.BUILD, cmd, out[0].adapter if out else "generic")

    return out


def _as_kind(val: CheckKind | str) -> CheckKind:
    if isinstance(val, CheckKind):
        return val
    return CheckKind(str(val))


def select_check(checks: list[CheckCommand], kind: CheckKind | str) -> Optional[CheckCommand]:
    k = _as_kind(kind)
    for c in checks:
        if c.kind == k:
            return c
    return None


async def run_check(
    check: CheckCommand,
    *,
    runner: CommandRunner,
    ctx: Optional[dict] = None,
) -> CheckResult:
    ctx = dict(ctx or {})
    ctx.setdefault("timeout_s", check.timeout_s)
    raw = await runner(check.command, ctx)
    code = int(raw.get("exit_code") if raw.get("exit_code") is not None else (0 if raw.get("ok") else 1))
    stdout = str(raw.get("stdout") or "")
    stderr = str(raw.get("stderr") or "")
    ok = bool(raw.get("ok")) and code == 0
    failure = None if ok else parse_failure(check.adapter, stdout, stderr, code)
    return CheckResult(
        ok=ok,
        command=check.command,
        kind=check.kind.value,
        adapter=check.adapter,
        exit_code=code,
        stdout=stdout,
        stderr=stderr,
        failure=failure,
    )


async def debug_check_loop(
    *,
    fs,
    kind: CheckKind | str = CheckKind.TEST,
    runner: CommandRunner,
    edit_hook: Optional[EditHook] = None,
    max_attempts: int = 3,
    ctx: Optional[dict] = None,
    preferred_command: Optional[str] = None,
) -> DebugLoopResult:
    """
    Detect → run → parse → optional edit → rerun until success or budget exhausted.
    Never infinite; max_attempts hard bound.
    """
    max_attempts = max(1, min(int(max_attempts), 10))
    checks = detect_checks(fs)
    k = _as_kind(kind)
    check = None
    if preferred_command:
        check = CheckCommand(kind=k, command=preferred_command, adapter="generic")
    else:
        check = select_check(checks, k)
    if check is None:
        return DebugLoopResult(
            ok=False,
            attempts=0,
            max_attempts=max_attempts,
            final={"error": "no_check_command_detected", "kind": k.value},
            exhausted=True,
        )

    history: list[dict] = []
    files_touched: list[str] = []
    last: Optional[CheckResult] = None

    for attempt in range(1, max_attempts + 1):
        result = await run_check(check, runner=runner, ctx=ctx)
        last = result
        entry = {"attempt": attempt, "result": result.to_dict()}
        history.append(entry)
        if result.ok:
            return DebugLoopResult(
                ok=True,
                attempts=attempt,
                max_attempts=max_attempts,
                final=result.to_dict(),
                history=history,
                exhausted=False,
                files_touched=files_touched,
            )
        # diagnose + optional repair
        failure = result.failure or parse_failure(check.adapter, result.stdout, result.stderr, result.exit_code)
        entry["failure"] = failure.to_dict()
        if edit_hook and attempt < max_attempts:
            try:
                edit = await edit_hook(failure.likely_files, failure)
                entry["edit"] = {
                    "ok": bool(edit.get("ok")),
                    "files_changed": list(edit.get("files_changed") or []),
                }
                for f in edit.get("files_changed") or []:
                    if f not in files_touched:
                        files_touched.append(f)
            except Exception as e:
                entry["edit"] = {"ok": False, "error": type(e).__name__}

    return DebugLoopResult(
        ok=False,
        attempts=max_attempts,
        max_attempts=max_attempts,
        final=last.to_dict() if last else None,
        history=history,
        exhausted=True,
        files_touched=files_touched,
    )


async def _default_runner(command: str, ctx: dict) -> dict:
    try:
        from execution.runner import run_command_in_project
        result = await run_command_in_project(
            user_id=ctx.get("user_id") or "system",
            project_id=ctx.get("project_id") or "default",
            command=command,
            timeout_s=int(ctx.get("timeout_s") or 180),
        )
        if isinstance(result, dict):
            code = int(result.get("exit_code") if result.get("exit_code") is not None else (0 if result.get("ok") else 1))
            return {
                "ok": code == 0,
                "exit_code": code,
                "stdout": str(result.get("stdout") or result.get("output") or "")[:12000],
                "stderr": str(result.get("stderr") or result.get("error") or "")[:6000],
                "command": command,
            }
    except Exception as e:
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": f"runner_unavailable:{type(e).__name__}",
            "command": command,
        }
    return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "no_result", "command": command}
