"""
Governed project bootstrap for the coding agent.

One toolchain abstraction — not per-framework orchestration forks.

  detect toolchain → scaffold structure → governed install → capture output
  → detect failures → optional repair → build/test → validate → evidence

Scaffolding alone never means the project "works".
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("devos.project_bootstrap")

CommandRunner = Callable[[str, dict], Awaitable[dict]]
# runner(command, ctx) -> {ok, exit_code, stdout, stderr}


class ToolchainKind(str, Enum):
    PYTHON = "python"
    NODE = "node"
    VITE = "vite"
    NEXTJS = "nextjs"
    ANGULAR = "angular"
    HTML = "html"


@dataclass
class ToolchainProfile:
    """Common project/toolchain abstraction."""

    kind: ToolchainKind
    runtime: str  # python3 | node | browser
    package_manager: str  # pip | npm | none
    install_cmd: Optional[str]
    build_cmd: Optional[str]
    test_cmd: Optional[str]
    validate_files: list[str]
    repair_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


PROFILES: dict[ToolchainKind, ToolchainProfile] = {
    ToolchainKind.PYTHON: ToolchainProfile(
        kind=ToolchainKind.PYTHON,
        runtime="python3",
        package_manager="pip",
        install_cmd="python -m pip install -r requirements.txt",
        build_cmd="python -m compileall -q .",
        test_cmd="python -m pytest -q",
        validate_files=["main.py", "requirements.txt", "README.md"],
        repair_hints=["pip install --upgrade pip", "python -m pip install -r requirements.txt"],
    ),
    ToolchainKind.NODE: ToolchainProfile(
        kind=ToolchainKind.NODE,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        validate_files=["package.json", "src/index.js", "README.md"],
        repair_hints=["rm -rf node_modules package-lock.json", "npm install --no-audit --no-fund"],
    ),
    ToolchainKind.VITE: ToolchainProfile(
        kind=ToolchainKind.VITE,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm run build",
        validate_files=["package.json", "index.html", "vite.config.js", "src/main.js"],
        repair_hints=["npm install --no-audit --no-fund"],
    ),
    ToolchainKind.NEXTJS: ToolchainProfile(
        kind=ToolchainKind.NEXTJS,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm run lint",
        validate_files=["package.json", "next.config.mjs", "app/page.jsx", "app/layout.jsx"],
        repair_hints=["npm install --no-audit --no-fund"],
    ),
    ToolchainKind.ANGULAR: ToolchainProfile(
        kind=ToolchainKind.ANGULAR,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watch=false --browsers=ChromeHeadless",
        validate_files=["package.json", "angular.json", "src/main.ts", "src/app/app.component.ts"],
        repair_hints=["npm install --no-audit --no-fund"],
    ),
    ToolchainKind.HTML: ToolchainProfile(
        kind=ToolchainKind.HTML,
        runtime="browser",
        package_manager="none",
        install_cmd=None,
        build_cmd=None,
        test_cmd=None,
        validate_files=["index.html", "style.css", "script.js", "README.md"],
        repair_hints=[],
    ),
}


def detect_toolchain(request: str, explicit: Optional[str] = None) -> ToolchainKind:
    if explicit:
        key = explicit.lower().strip().replace(".", "").replace(" ", "")
        for k in ToolchainKind:
            if k.value.replace("_", "") == key or k.value == explicit.lower():
                return k
        aliases = {
            "nodejs": ToolchainKind.NODE,
            "js": ToolchainKind.NODE,
            "javascript": ToolchainKind.NODE,
            "py": ToolchainKind.PYTHON,
            "next": ToolchainKind.NEXTJS,
            "static": ToolchainKind.HTML,
            "htmlcssjs": ToolchainKind.HTML,
        }
        if key in aliases:
            return aliases[key]

    t = (request or "").lower()
    if re.search(r"\bnext(?:\.?js)?\b", t):
        return ToolchainKind.NEXTJS
    if re.search(r"\bangular\b", t):
        return ToolchainKind.ANGULAR
    if re.search(r"\bvite\b", t):
        return ToolchainKind.VITE
    if re.search(r"\bpython\b|\bflask\b|\bfastapi\b|\bdjango\b", t):
        return ToolchainKind.PYTHON
    if re.search(r"\bhtml\b|\bcss\b|static site|landing page", t):
        return ToolchainKind.HTML
    if re.search(r"\bnode\b|\bnpm\b|\bexpress\b", t):
        return ToolchainKind.NODE
    return ToolchainKind.HTML


def _templates(kind: ToolchainKind, name: str) -> dict[str, str]:
    """Minimal viable file set per toolchain (not framework-specific engines)."""
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "app"
    if kind == ToolchainKind.PYTHON:
        return {
            "main.py": (
                f'"""{safe} — generated by DevOS project bootstrap."""\n\n'
                "def main() -> None:\n"
                f'    print("hello from {safe}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n"
            ),
            "requirements.txt": "# add dependencies here\n",
            "README.md": f"# {safe}\n\nPython project bootstrapped by DevOS.\n\n```bash\npip install -r requirements.txt\npython main.py\n```\n",
            "tests/test_main.py": "def test_placeholder():\n    assert True\n",
        }
    if kind == ToolchainKind.NODE:
        return {
            "package.json": json.dumps({
                "name": safe.lower(),
                "version": "0.1.0",
                "private": True,
                "type": "module",
                "scripts": {"start": "node src/index.js", "build": "node src/index.js", "test": "node --test"},
                "dependencies": {},
            }, indent=2) + "\n",
            "src/index.js": f'console.log("hello from {safe}");\n',
            "README.md": f"# {safe}\n\nNode.js project bootstrapped by DevOS.\n",
        }
    if kind == ToolchainKind.VITE:
        return {
            "package.json": json.dumps({
                "name": safe.lower(),
                "version": "0.1.0",
                "private": True,
                "type": "module",
                "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
                "devDependencies": {"vite": "^5.4.0"},
            }, indent=2) + "\n",
            "index.html": (
                "<!doctype html>\n<html lang=\"en\">\n<head>\n"
                "  <meta charset=\"UTF-8\" />\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
                f"  <title>{safe}</title>\n</head>\n<body>\n  <div id=\"app\"></div>\n"
                "  <script type=\"module\" src=\"/src/main.js\"></script>\n</body>\n</html>\n"
            ),
            "vite.config.js": "import { defineConfig } from 'vite';\nexport default defineConfig({});\n",
            "src/main.js": f'document.querySelector("#app").textContent = "hello from {safe}";\n',
            "README.md": f"# {safe}\n\nVite app bootstrapped by DevOS.\n",
        }
    if kind == ToolchainKind.NEXTJS:
        return {
            "package.json": json.dumps({
                "name": safe.lower(),
                "version": "0.1.0",
                "private": True,
                "scripts": {"dev": "next dev", "build": "next build", "start": "next start", "lint": "next lint"},
                "dependencies": {"next": "^14.2.0", "react": "^18.3.0", "react-dom": "^18.3.0"},
            }, indent=2) + "\n",
            "next.config.mjs": "/** @type {import('next').NextConfig} */\nconst nextConfig = {};\nexport default nextConfig;\n",
            "app/layout.jsx": (
                "export default function RootLayout({ children }) {\n"
                "  return (\n    <html lang=\"en\"><body>{children}</body></html>\n  );\n}\n"
            ),
            "app/page.jsx": f"export default function Page() {{\n  return <main><h1>{safe}</h1></main>;\n}}\n",
            "README.md": f"# {safe}\n\nNext.js app bootstrapped by DevOS.\n",
        }
    if kind == ToolchainKind.ANGULAR:
        return {
            "package.json": json.dumps({
                "name": safe.lower(),
                "version": "0.1.0",
                "private": True,
                "scripts": {"start": "ng serve", "build": "ng build", "test": "ng test"},
                "dependencies": {
                    "@angular/core": "^18.0.0",
                    "@angular/platform-browser": "^18.0.0",
                    "@angular/platform-browser-dynamic": "^18.0.0",
                    "rxjs": "^7.8.0",
                    "tslib": "^2.6.0",
                    "zone.js": "^0.14.0",
                },
                "devDependencies": {"@angular/cli": "^18.0.0", "typescript": "~5.4.0"},
            }, indent=2) + "\n",
            "angular.json": json.dumps({
                "version": 1,
                "projects": {safe: {"projectType": "application", "root": "", "sourceRoot": "src"}},
            }, indent=2) + "\n",
            "tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2022", "module": "ES2022"}}, indent=2) + "\n",
            "src/main.ts": "import { platformBrowserDynamic } from '@angular/platform-browser-dynamic';\nconsole.log('angular bootstrap placeholder');\n",
            "src/app/app.component.ts": (
                "import { Component } from '@angular/core';\n"
                "@Component({ selector: 'app-root', template: '<h1>" + safe + "</h1>', standalone: true })\n"
                "export class AppComponent {}\n"
            ),
            "README.md": f"# {safe}\n\nAngular app bootstrapped by DevOS.\n",
        }
    # HTML default
    return {
        "index.html": (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "  <meta charset=\"utf-8\" />\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
            f"  <title>{safe}</title>\n  <link rel=\"stylesheet\" href=\"style.css\" />\n"
            "</head>\n<body>\n  <main>\n    <h1>" + safe + "</h1>\n"
            "    <p>Static site bootstrapped by DevOS.</p>\n  </main>\n"
            "  <script src=\"script.js\"></script>\n</body>\n</html>\n"
        ),
        "style.css": "body{font-family:system-ui,sans-serif;margin:2rem;background:#0b0d10;color:#e8eaed}\n",
        "script.js": "console.log('ready');\n",
        "README.md": f"# {safe}\n\nStatic HTML/CSS/JS site bootstrapped by DevOS.\n",
    }


@dataclass
class BootstrapResult:
    ok: bool
    toolchain: str
    profile: dict
    files_written: list[str] = field(default_factory=list)
    scaffold_ok: bool = False
    install: Optional[dict] = None
    repair: Optional[dict] = None
    build: Optional[dict] = None
    test: Optional[dict] = None
    validation: Optional[dict] = None
    evidence_id: Optional[str] = None
    artifact_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    claimed_working: bool = False  # always False unless validation proves it

    def to_dict(self) -> dict:
        return asdict(self)


def _evidence(action: str, actor: str, status: str, meta: dict) -> str:
    eid = f"bootstrap-{uuid.uuid4().hex[:12]}"
    try:
        from governance.evidence import EvidenceNode, EvidenceChainManager
        mgr = EvidenceChainManager()
        chain = mgr.get_or_create_chain(
            chain_id=f"bootstrap-{meta.get('tenant_id', 'default')}",
            label="project_bootstrap",
        )
        node = EvidenceNode(
            node_id=eid,
            chain_id=getattr(chain, "chain_id", "bootstrap"),
            action=action,
            actor_id=actor or "agent",
            status=status,
            metadata=meta,
        )
        if hasattr(chain, "add_node"):
            chain.add_node(node)
    except Exception:
        pass
    return eid


async def _default_runner(command: str, ctx: dict) -> dict:
    """Governed command path via execution.runner when available."""
    try:
        from execution.runner import run_command_in_project
        user_id = ctx.get("user_id") or "system"
        project_id = ctx.get("project_id") or "default"
        result = await run_command_in_project(
            user_id=user_id,
            project_id=project_id,
            command=command,
            timeout_s=int(ctx.get("timeout_s") or 120),
        )
        if isinstance(result, dict):
            code = int(result.get("exit_code") if result.get("exit_code") is not None else (0 if result.get("ok") else 1))
            return {
                "ok": code == 0,
                "exit_code": code,
                "stdout": str(result.get("stdout") or result.get("output") or "")[:8000],
                "stderr": str(result.get("stderr") or result.get("error") or "")[:4000],
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


def scaffold_project(
    *,
    fs,
    kind: ToolchainKind,
    project_name: str,
) -> tuple[list[str], list[str]]:
    """Write template files via FileService. Returns (written_paths, errors)."""
    files = _templates(kind, project_name)
    written: list[str] = []
    errors: list[str] = []
    for path, content in files.items():
        try:
            fs.write(path, content)
            written.append(path)
        except Exception as e:
            errors.append(f"{path}:{type(e).__name__}")
    return written, errors


def validate_project(fs, profile: ToolchainProfile, written: list[str]) -> dict:
    """Structural validation — not a claim that the app runs in production."""
    missing = []
    for rel in profile.validate_files:
        try:
            data = fs.read(rel)
            content = (data or {}).get("content") if isinstance(data, dict) else data
            if content is None or content == "":
                # tree check
                tree = {i["path"] for i in (fs.tree() or []) if i.get("type") == "file"}
                if rel not in tree and rel not in written:
                    missing.append(rel)
        except Exception:
            tree = set()
            try:
                tree = {i["path"] for i in (fs.tree() or []) if i.get("type") == "file"}
            except Exception:
                pass
            if rel not in tree and rel not in written:
                missing.append(rel)

    structure_ok = len(missing) == 0 and len(written) > 0
    return {
        "structure_ok": structure_ok,
        "missing_files": missing,
        "files_checked": list(profile.validate_files),
        "scaffold_only": True,
        "works": False,  # never true from structure alone
        "message": (
            "Project structure present; install/build/test not yet proven"
            if structure_ok else
            f"Missing required files: {missing}"
        ),
    }


async def bootstrap_project(
    *,
    user_id: str,
    project_id: str,
    project_name: str,
    request: str = "",
    toolchain: Optional[str] = None,
    run_install: bool = True,
    run_build: bool = True,
    run_test: bool = False,
    allow_repair: bool = True,
    command_runner: Optional[CommandRunner] = None,
    actor_id: str = "coding_agent",
    tenant_id: str = "default",
) -> BootstrapResult:
    """
    Full governed bootstrap pipeline.

    claimed_working is True only when install (if required) and validation pass
    and optional build/test succeed when requested.
    """
    kind = detect_toolchain(request, explicit=toolchain)
    profile = PROFILES[kind]
    runner = command_runner or _default_runner
    ctx = {"user_id": user_id, "project_id": project_id, "timeout_s": 180}

    from execution.files import FileService
    fs = FileService(user_id, project_id)

    result = BootstrapResult(
        ok=False,
        toolchain=kind.value,
        profile=profile.to_dict(),
    )

    written, sc_errors = scaffold_project(fs=fs, kind=kind, project_name=project_name)
    result.files_written = written
    result.artifact_refs = [f"file:{p}" for p in written]
    result.errors.extend(sc_errors)
    result.scaffold_ok = len(written) > 0 and not sc_errors
    if not result.scaffold_ok:
        result.errors.append("scaffold_failed")
        result.evidence_id = _evidence(
            "project.bootstrap", actor_id, "failed",
            {"stage": "scaffold", "toolchain": kind.value, "tenant_id": tenant_id},
        )
        return result

    # Install (governed command) when toolchain needs dependencies
    if run_install and profile.install_cmd:
        install = await runner(profile.install_cmd, ctx)
        result.install = install
        if not install.get("ok"):
            if allow_repair and profile.repair_hints:
                repair_out = None
                for hint in profile.repair_hints[:2]:
                    repair_out = await runner(hint, ctx)
                    result.repair = repair_out
                    if repair_out.get("ok"):
                        # re-run primary install once
                        install = await runner(profile.install_cmd, ctx)
                        result.install = install
                        break
            if not (result.install or {}).get("ok"):
                result.errors.append("install_failed")
                result.validation = validate_project(fs, profile, written)
                result.evidence_id = _evidence(
                    "project.bootstrap", actor_id, "failed",
                    {
                        "stage": "install",
                        "toolchain": kind.value,
                        "exit_code": (result.install or {}).get("exit_code"),
                        "tenant_id": tenant_id,
                    },
                )
                # scaffold may have succeeded — still not "working"
                result.ok = False
                result.claimed_working = False
                return result

    if run_build and profile.build_cmd:
        build = await runner(profile.build_cmd, ctx)
        result.build = build
        if not build.get("ok"):
            result.errors.append("build_failed")

    if run_test and profile.test_cmd:
        test = await runner(profile.test_cmd, ctx)
        result.test = test
        if not test.get("ok"):
            result.errors.append("test_failed")

    validation = validate_project(fs, profile, written)
    # Prove more than scaffold when commands ran
    install_ok = (not profile.install_cmd) or (not run_install) or bool((result.install or {}).get("ok"))
    build_ok = (not profile.build_cmd) or (not run_build) or bool((result.build or {}).get("ok"))
    test_ok = (not run_test) or (not profile.test_cmd) or bool((result.test or {}).get("ok"))

    validation["install_ok"] = install_ok
    validation["build_ok"] = build_ok
    validation["test_ok"] = test_ok
    # HTML/static: structure is delivery of files only — never "works" without runtime proof.
    if kind == ToolchainKind.HTML:
        validation["works"] = False
        validation["scaffold_only"] = True
        validation["message"] = (
            "Static files written; open index.html to verify in a browser "
            "(scaffold is not runtime proof)"
        )
    else:
        validation["works"] = bool(
            validation["structure_ok"] and install_ok and build_ok and test_ok
            and (not run_install or profile.install_cmd is None or install_ok)
        )
        if validation["works"]:
            validation["scaffold_only"] = False
            validation["message"] = "Structure + required commands succeeded"
        else:
            validation["scaffold_only"] = not install_ok or not validation["structure_ok"]
    result.validation = validation

    result.claimed_working = bool(validation.get("works"))
    result.ok = result.scaffold_ok and install_ok and (
        # HTML: structure is enough for ok=true of bootstrap delivery, still claimed_working only if works
        kind == ToolchainKind.HTML or install_ok
    )
    if result.errors and kind != ToolchainKind.HTML:
        result.ok = result.scaffold_ok and install_ok and "install_failed" not in result.errors

    # Tighten: ok means bootstrap pipeline finished without hard failure
    hard_fail = "scaffold_failed" in result.errors or "install_failed" in result.errors
    result.ok = result.scaffold_ok and not hard_fail
    # Never claim working on scaffold alone
    if not validation.get("works"):
        result.claimed_working = False

    result.evidence_id = _evidence(
        "project.bootstrap",
        actor_id,
        "success" if result.ok else "failed",
        {
            "toolchain": kind.value,
            "files": written,
            "claimed_working": result.claimed_working,
            "errors": result.errors,
            "tenant_id": tenant_id,
        },
    )
    return result
