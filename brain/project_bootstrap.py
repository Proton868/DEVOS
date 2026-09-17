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
    JAVASCRIPT = "javascript"  # alias of node for detection
    TYPESCRIPT = "typescript"
    VITE = "vite"
    NEXTJS = "nextjs"
    ANGULAR = "angular"
    HTML = "html"
    SHELL = "shell"  # Makefile / shell scripts
    # Extension kinds (e.g. flutter) register via register_toolchain_profile


@dataclass
class ToolchainProfile:
    """Common project/toolchain abstraction — single execution surface."""

    kind: ToolchainKind | str
    runtime: str  # binary name: python3 | node | browser | make | flutter
    package_manager: str  # pip | npm | none | flutter
    install_cmd: Optional[str]
    build_cmd: Optional[str]
    test_cmd: Optional[str]
    validate_files: list[str]
    repair_hints: list[str] = field(default_factory=list)
    typecheck_cmd: Optional[str] = None
    lint_cmd: Optional[str] = None
    required_binaries: list[str] = field(default_factory=list)
    # Files that indicate this ecosystem is present on disk
    detect_markers: list[str] = field(default_factory=list)
    # If True, install must succeed before claimed_working
    requires_install: bool = True

    def kind_value(self) -> str:
        k = self.kind
        return k.value if isinstance(k, ToolchainKind) else str(k)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind_value()
        return d


PROFILES: dict[str, ToolchainProfile] = {}


def _put_profile(profile: ToolchainProfile) -> None:
    key = profile.kind_value()
    PROFILES[key] = profile
    # Enum alias when applicable
    try:
        PROFILES[ToolchainKind(key)] = profile  # type: ignore[index]
    except Exception:
        pass


def register_toolchain_profile(profile: ToolchainProfile) -> ToolchainProfile:
    """Extension point for additional ecosystems (e.g. Flutter) without a new agent."""
    if not profile.required_binaries and profile.runtime and profile.runtime != "browser":
        profile.required_binaries = [profile.runtime]
    _put_profile(profile)
    return profile


def _seed_builtin_profiles() -> None:
    if PROFILES:
        return
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.PYTHON,
        runtime="python3",
        package_manager="pip",
        install_cmd="python3 -m pip install -r requirements.txt",
        build_cmd="python3 -m compileall -q .",
        test_cmd="python3 -m pytest -q",
        typecheck_cmd=None,
        lint_cmd=None,
        validate_files=["main.py", "requirements.txt", "README.md"],
        repair_hints=["python3 -m pip install --upgrade pip", "python3 -m pip install -r requirements.txt"],
        required_binaries=["python3"],
        detect_markers=["requirements.txt", "pyproject.toml", "setup.py", "main.py"],
        requires_install=True,
    ))
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.NODE,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        typecheck_cmd=None,
        lint_cmd=None,
        validate_files=["package.json", "src/index.js", "README.md"],
        repair_hints=["rm -rf node_modules package-lock.json", "npm install --no-audit --no-fund"],
        required_binaries=["node", "npm"],
        detect_markers=["package.json"],
        requires_install=True,
    ))
    # JavaScript shares Node profile (same runtime)
    js = ToolchainProfile(
        kind=ToolchainKind.JAVASCRIPT,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        validate_files=["package.json", "src/index.js", "README.md"],
        repair_hints=["npm install --no-audit --no-fund"],
        required_binaries=["node", "npm"],
        detect_markers=["package.json", "src/index.js"],
        requires_install=True,
    )
    register_toolchain_profile(js)
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.TYPESCRIPT,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        typecheck_cmd="npx tsc --noEmit",
        lint_cmd="npx eslint . --max-warnings 0",
        validate_files=["package.json", "tsconfig.json", "README.md"],
        repair_hints=["npm install --no-audit --no-fund", "npx tsc --noEmit"],
        required_binaries=["node", "npm"],
        detect_markers=["tsconfig.json", "package.json"],
        requires_install=True,
    ))
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.VITE,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        typecheck_cmd="npx tsc --noEmit",
        validate_files=["package.json", "vite.config.js", "index.html", "README.md"],
        repair_hints=["npm install --no-audit --no-fund"],
        required_binaries=["node", "npm"],
        detect_markers=["vite.config.js", "vite.config.ts", "package.json"],
        requires_install=True,
    ))
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.NEXTJS,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        typecheck_cmd="npx tsc --noEmit",
        validate_files=["package.json", "next.config.js", "README.md"],
        repair_hints=["npm install --no-audit --no-fund"],
        required_binaries=["node", "npm"],
        detect_markers=["next.config.js", "next.config.mjs", "next.config.ts"],
        requires_install=True,
    ))
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.ANGULAR,
        runtime="node",
        package_manager="npm",
        install_cmd="npm install --no-audit --no-fund",
        build_cmd="npm run build",
        test_cmd="npm test -- --watchAll=false",
        validate_files=["package.json", "angular.json", "README.md"],
        repair_hints=["npm install --no-audit --no-fund"],
        required_binaries=["node", "npm"],
        detect_markers=["angular.json"],
        requires_install=True,
    ))
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.HTML,
        runtime="browser",
        package_manager="none",
        install_cmd=None,
        build_cmd=None,
        test_cmd=None,
        validate_files=["index.html", "style.css", "script.js", "README.md"],
        repair_hints=[],
        required_binaries=[],  # no host runtime required for static files
        detect_markers=["index.html"],
        requires_install=False,
    ))
    register_toolchain_profile(ToolchainProfile(
        kind=ToolchainKind.SHELL,
        runtime="make",
        package_manager="none",
        install_cmd=None,
        build_cmd="make",
        test_cmd="make test",
        validate_files=["Makefile", "README.md"],
        repair_hints=[],
        required_binaries=["make"],
        detect_markers=["Makefile", "makefile"],
        requires_install=False,
    ))
    # Flutter: project deps via governed execution; SDK install is never silent.
    # Missing flutter binary → fail-closed report (required_binaries), not apt/sudo.
    register_toolchain_profile(ToolchainProfile(
        kind="flutter",
        runtime="flutter",
        package_manager="flutter",
        install_cmd="flutter pub get",
        build_cmd="flutter build apk --debug",
        test_cmd="flutter test",
        typecheck_cmd="flutter analyze",
        lint_cmd="dart analyze",
        validate_files=["pubspec.yaml", "lib/main.dart", "README.md"],
        repair_hints=[
            "Ensure Flutter SDK is installed (PATH or data/toolchains/flutter)",
            "Run flutter doctor and resolve reported issues",
            "Use authorized ucip:toolchain.flutter_sdk_provision for governed SDK install",
            "Do not use apt/sudo/curl|bash from DevOS to install Flutter",
            "ios builds require macOS + Xcode",
        ],
        required_binaries=["flutter", "dart"],
        detect_markers=["pubspec.yaml", "lib/main.dart"],
        requires_install=True,
    ))


_seed_builtin_profiles()



def get_profile(kind: ToolchainKind | str) -> ToolchainProfile:
    _seed_builtin_profiles()
    key = kind.value if isinstance(kind, ToolchainKind) else str(kind).lower().strip()
    # aliases
    aliases = {
        "js": "javascript",
        "nodejs": "node",
        "py": "python",
        "ts": "typescript",
        "makefile": "shell",
        "make": "shell",
    }
    key = aliases.get(key, key)
    if key not in PROFILES:
        raise KeyError(f"unsupported_ecosystem:{key}")
    return PROFILES[key]


def detect_toolchain(request: str, explicit: Optional[str] = None) -> ToolchainKind | str:
    """Map user request / explicit name to a registered toolchain kind."""
    _seed_builtin_profiles()
    if explicit:
        key = explicit.lower().strip().replace(" ", "")
        aliases = {
            "nodejs": "node",
            "js": "javascript",
            "javascript": "javascript",
            "py": "python",
            "ts": "typescript",
            "typescript": "typescript",
            "makefile": "shell",
            "make": "shell",
            "sh": "shell",
            "flutter": "flutter",
            "dart": "flutter",
        }
        key = aliases.get(key, key)
        if key in PROFILES:
            try:
                return ToolchainKind(key)
            except Exception:
                return key
        raise KeyError(f"unsupported_ecosystem:{key}")

    text = (request or "").lower()
    rules = [
        (ToolchainKind.NEXTJS, ("next.js", "nextjs", "next app")),
        (ToolchainKind.ANGULAR, ("angular",)),
        (ToolchainKind.VITE, ("vite",)),
        (ToolchainKind.TYPESCRIPT, ("typescript", " ts ", ".ts ")),
        (ToolchainKind.SHELL, ("makefile", " make ", "shell script")),
        ("flutter", ("flutter", "dart mobile", "android app", "ios app", "pubspec")),
        (ToolchainKind.PYTHON, ("python", "pytest", "django", "flask", "fastapi")),
        (ToolchainKind.NODE, ("node.js", "nodejs", "express", "npm ")),
        (ToolchainKind.JAVASCRIPT, ("javascript",)),
        (ToolchainKind.HTML, ("html", "static site", "landing page", "website")),
    ]
    for kind, keys in rules:
        if any(k in text for k in keys):
            return kind
    return ToolchainKind.HTML


def detect_project_toolchain(fs) -> dict:
    """
    Infer toolchain from workspace files — does NOT claim runtime availability.
    """
    _seed_builtin_profiles()
    try:
        tree = fs.tree(max_depth=4) if hasattr(fs, "tree") else []
        paths = {
            (i.get("path") or "").replace("\\", "/").lstrip("./")
            for i in (tree or [])
            if i.get("type") == "file"
        }
    except Exception:
        paths = set()

    try:
        from execution.flutter_toolchain import detect_flutter_project
        fl = detect_flutter_project(fs)
        if fl.is_flutter_project:
            profile = PROFILES.get("flutter")
            return {
                "kind": "flutter",
                "profile": profile.to_dict() if profile else None,
                "markers_found": ["pubspec.yaml"] + list(fl.structure_markers),
                "score": int(fl.confidence * 10),
                "message": "detected:flutter",
                "flutter": fl.to_dict(),
            }
    except Exception:
        pass

    scores: dict[str, int] = {}
    for key, profile in list(PROFILES.items()):
        if not isinstance(key, str):
            continue
        score = 0
        for marker in profile.detect_markers or []:
            if marker in paths or any(p.endswith("/" + marker) or p == marker for p in paths):
                score += 2
            # basename match
            for p in paths:
                if p.split("/")[-1] == marker:
                    score += 2
                    break
        if score:
            scores[key] = scores.get(key, 0) + score
    if not scores:
        return {
            "kind": None,
            "profile": None,
            "markers_found": [],
            "message": "no_known_project_markers",
        }
    best = max(scores, key=scores.get)
    profile = PROFILES[best]
    return {
        "kind": best,
        "profile": profile.to_dict(),
        "markers_found": list(profile.detect_markers or []),
        "score": scores[best],
        "message": f"detected:{best}",
    }


def check_runtime_available(
    profile: ToolchainProfile,
    *,
    which_fn=None,
) -> dict:
    """
    Verify host binaries exist. Never claims availability from project files alone.
    """
    import shutil

    which = which_fn or shutil.which
    required = list(profile.required_binaries or [])
    if not required and profile.runtime and profile.runtime != "browser":
        required = [profile.runtime]

    if profile.kind_value() == "flutter" and which_fn is None:
        try:
            from execution.flutter_toolchain import probe_flutter_runtime, flutter_command_plan
            info = probe_flutter_runtime()
            out = {
                "ok": bool(info.flutter_available),
                "runtime": profile.runtime,
                "required_binaries": required,
                "present": [],
                "missing": [],
                "toolchain": "flutter",
                "flutter": info.to_dict(),
                "versions": {"flutter": info.flutter_version, "dart": info.dart_version, "channel": info.channel},
                "plan": flutter_command_plan(),
                "platform_limits": info.platform_limits,
            }
            if info.flutter_path:
                out["present"].append({"binary": "flutter", "path": info.flutter_path})
            else:
                out["missing"].append("flutter")
            if info.dart_path:
                out["present"].append({"binary": "dart", "path": info.dart_path})
            elif "dart" in required:
                out["missing"].append("dart")
            if not out["ok"]:
                out["error"] = "toolchain_unavailable"
                out["message"] = (
                    "Flutter SDK not available. Use authorized "
                    "ucip:toolchain.flutter_sdk_provision or install on PATH. "
                    "DevOS will not run apt/sudo/curl|bash to install Flutter."
                )
            return out
        except Exception as e:
            logger.debug("flutter probe failed: %s", e)

    missing = []
    present = []
    for bin_name in required:
        path = which(bin_name)
        if path:
            present.append({"binary": bin_name, "path": path})
        else:
            missing.append(bin_name)

    ok = len(missing) == 0
    out = {
        "ok": ok,
        "runtime": profile.runtime,
        "required_binaries": required,
        "present": present,
        "missing": missing,
        "toolchain": profile.kind_value(),
    }
    if not ok:
        out["error"] = "toolchain_unavailable"
        out["message"] = (
            f"Required toolchain binaries missing: {', '.join(missing)}. "
            "DevOS will not silently install system-level software."
        )
    return out


def execution_plan(profile: ToolchainProfile) -> dict:
    """Structured governed commands for this toolchain."""
    cmds = {
        "install": profile.install_cmd,
        "build": profile.build_cmd,
        "test": profile.test_cmd,
        "typecheck": profile.typecheck_cmd,
        "lint": profile.lint_cmd,
    }
    return {
        "toolchain": profile.kind_value(),
        "runtime": profile.runtime,
        "package_manager": profile.package_manager,
        "commands": {k: v for k, v in cmds.items() if v},
        "validate_files": list(profile.validate_files or []),
        "requires_install": bool(profile.requires_install),
        "repair_hints": list(profile.repair_hints or []),
    }


def resolve_toolchain(
    *,
    request: str = "",
    explicit: Optional[str] = None,
    fs=None,
) -> dict:
    """
    Full resolution: what project, what toolchain, is runtime available, what commands.
    """
    detected = detect_project_toolchain(fs) if fs is not None else None
    try:
        kind = detect_toolchain(request, explicit=explicit)
    except KeyError as e:
        return {
            "ok": False,
            "error": "unsupported_ecosystem",
            "message": str(e),
            "detected": detected,
        }
    # Prefer workspace detection when no explicit request signal
    if detected and detected.get("kind") and not explicit and not (request or "").strip():
        kind = detected["kind"]
    try:
        profile = get_profile(kind)
    except KeyError as e:
        return {
            "ok": False,
            "error": "unsupported_ecosystem",
            "message": str(e),
            "detected": detected,
        }
    runtime = check_runtime_available(profile)
    plan = execution_plan(profile)
    return {
        "ok": runtime["ok"],
        "kind": profile.kind_value(),
        "profile": profile.to_dict(),
        "runtime": runtime,
        "plan": plan,
        "detected": detected,
        "error": None if runtime["ok"] else "toolchain_unavailable",
        "message": runtime.get("message") if not runtime["ok"] else "toolchain_ready",
    }


def _templates(kind: ToolchainKind | str, name: str) -> dict[str, str]:
    """Minimal viable file set per toolchain (not framework-specific engines)."""
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "app"
    kind_key = kind.value if isinstance(kind, ToolchainKind) else str(kind)
    if kind_key == "flutter":
        class_name = "".join(part.title() for part in safe.replace("-", "_").split("_") if part) or "App"
        pkg = safe.lower().replace("-", "_")
        return {
            "pubspec.yaml": (
                f"name: {pkg}\n"
                "description: Flutter project bootstrapped by DevOS.\n"
                "publish_to: 'none'\n"
                "version: 0.1.0+1\n\n"
                "environment:\n"
                "  sdk: '>=3.0.0 <4.0.0'\n\n"
                "dependencies:\n"
                "  flutter:\n"
                "    sdk: flutter\n\n"
                "dev_dependencies:\n"
                "  flutter_test:\n"
                "    sdk: flutter\n\n"
                "flutter:\n"
                "  uses-material-design: true\n"
            ),
            "lib/main.dart": (
                "import 'package:flutter/material.dart';\n\n"
                f"void main() => runApp(const {class_name}App());\n\n"
                f"class {class_name}App extends StatelessWidget {{\n"
                f"  const {class_name}App({{super.key}});\n\n"
                "  @override\n"
                "  Widget build(BuildContext context) {\n"
                "    return MaterialApp(\n"
                f"      title: '{safe}',\n"
                "      home: const Scaffold(\n"
                f"        body: Center(child: Text('hello from {safe}')),\n"
                "      ),\n"
                "    );\n"
                "  }\n"
                "}\n"
            ),
            "test/widget_test.dart": (
                "import 'package:flutter_test/flutter_test.dart';\n\n"
                "void main() {\n"
                "  test('placeholder', () {\n"
                "    expect(1 + 1, 2);\n"
                "  });\n"
                "}\n"
            ),
            "README.md": (
                f"# {safe}\n\nFlutter project bootstrapped by DevOS.\n\n"
                "```bash\nflutter pub get\nflutter analyze\nflutter test\n```\n\n"
                "Note: `flutter build ios` requires macOS + Xcode.\n"
            ),
        }
    if kind_key in ("javascript", ToolchainKind.JAVASCRIPT.value):
        kind = ToolchainKind.NODE  # same scaffold as node
    if kind_key == ToolchainKind.TYPESCRIPT.value or kind is ToolchainKind.TYPESCRIPT:
        return {
            "package.json": json.dumps({
                "name": safe.lower(),
                "version": "0.1.0",
                "private": True,
                "scripts": {"build": "tsc", "test": "echo \"no tests\""},
                "devDependencies": {"typescript": "^5.4.0"},
            }, indent=2) + "\n",
            "tsconfig.json": json.dumps({
                "compilerOptions": {"target": "ES2020", "module": "commonjs", "strict": True, "outDir": "dist"},
                "include": ["src/**/*"],
            }, indent=2) + "\n",
            "src/index.ts": f'console.log("hello from {safe}");\n',
            "README.md": f"# {safe}\n\nTypeScript project bootstrapped by DevOS.\n",
        }
    if kind_key == ToolchainKind.SHELL.value or kind is ToolchainKind.SHELL:
        return {
            "Makefile": (
                f".PHONY: all test\nall:\n\t@echo hello from {safe}\n"
                "test:\n\t@echo ok\n"
            ),
            "README.md": f"# {safe}\n\nMakefile project bootstrapped by DevOS.\n",
            "scripts/run.sh": "#!/bin/sh\necho hello\n",
        }
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
    error_code: Optional[str] = None  # primary fail-closed code when not fully working

    def to_dict(self) -> dict:
        return asdict(self)


def _evidence(action: str, actor: str, status: str, meta: dict) -> str:
    eid = f"bootstrap-{uuid.uuid4().hex[:12]}"
    try:
        from governance.evidence import EvidenceChain, EvidenceChainManager
        chain_id = f"bootstrap-{meta.get('tenant_id') or meta.get('user_id') or 'default'}"
        chain = EvidenceChainManager.load(chain_id)
        if chain is None:
            chain = EvidenceChain(
                chain_id=chain_id,
                goal="project_bootstrap",
                identity_context={
                    "user_id": str(meta.get("user_id") or ""),
                    "actor_id": actor or "agent",
                    "tenant_id": str(meta.get("tenant_id") or ""),
                },
            )
        chain.add_node(
            action=action,
            actor_id=actor or "agent",
            status=status,
            metadata={**dict(meta or {}), "evidence_id": eid},
        )
        chain.save()
    except Exception:
        pass
    return eid


async def _default_runner(command: str, ctx: dict) -> dict:
    """Governed command path via execution.runner when available.

    Project bootstrap/install/build/test commands are untrusted by default
    (uploaded or AI-generated project trees). Isolation is mandatory.
    """
    try:
        from execution.runner import run_command_in_project
        user_id = ctx.get("user_id") or "system"
        project_id = ctx.get("project_id") or "default"
        result = await run_command_in_project(
            user_id=user_id,
            project_id=project_id,
            command=command,
            timeout_s=int(ctx.get("timeout_s") or 120),
            policy=ctx.get("isolation_policy") or "untrusted",
            source="project_bootstrap",
            allow_network=bool(ctx.get("allow_network", False)),
        )
        if isinstance(result, dict):
            if result.get("status") == "isolation_unavailable":
                return {
                    "ok": False,
                    "exit_code": 126,
                    "stdout": "",
                    "stderr": str(result.get("stderr") or "isolation_unavailable"),
                    "command": command,
                    "status": "isolation_unavailable",
                    "isolation_evidence": result.get("isolation_evidence"),
                }
            code = int(result.get("exit_code") if result.get("exit_code") is not None else (0 if result.get("ok") else 1))
            out = {
                "ok": code == 0 and result.get("ok", code == 0),
                "exit_code": code,
                "stdout": str(result.get("stdout") or result.get("output") or "")[:8000],
                "stderr": str(result.get("stderr") or result.get("error") or "")[:4000],
                "command": command,
            }
            if result.get("isolation_evidence"):
                out["isolation_evidence"] = result["isolation_evidence"]
            return out
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
    kind: ToolchainKind | str,
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



# Fail-closed error codes (stable contract for agents/UI)
ERR_TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
ERR_UNSUPPORTED_ECOSYSTEM = "unsupported_ecosystem"
ERR_INSTALL_FAILED = "install_failed"
ERR_BUILD_FAILED = "build_failed"
ERR_TEST_FAILED = "test_failed"
ERR_SCAFFOLD_FAILED = "scaffold_failed"
ERR_PATH_ESCAPE = "path_escape"
ERR_SYSTEM_INSTALL_BLOCKED = "system_install_blocked"

# Commands that would install system packages — never auto-run by bootstrap.
_SYSTEM_INSTALL_PATTERNS = (
    "apt-get ", "apt install", "yum install", "dnf install", "pacman -S",
    "brew install", "choco install", "winget install", "snap install",
    "sudo ",
)


def is_system_level_install_command(command: str) -> bool:
    c = (command or "").strip().lower()
    return any(p in c for p in _SYSTEM_INSTALL_PATTERNS)


def apply_fail_closed_claims(
    result: "BootstrapResult",
    *,
    kind_value: str,
    profile: "ToolchainProfile",
    run_install: bool,
    run_build: bool,
    run_test: bool,
) -> "BootstrapResult":
    """
    Authoritative fail-closed finalizer.

    Invariants:
    - Missing binary → toolchain_unavailable; claimed_working=False
    - Unsupported ecosystem → unsupported_ecosystem; claimed_working=False
    - Install/build/test failure → claimed_working=False
    - HTML: never claimed_working (files delivered only)
    - Scaffold alone never claimed_working
    """
    errors = list(result.errors or [])
    validation = dict(result.validation or {})

    # Primary error_code from first hard failure
    for code in (
        ERR_UNSUPPORTED_ECOSYSTEM,
        ERR_TOOLCHAIN_UNAVAILABLE,
        ERR_SCAFFOLD_FAILED,
        ERR_INSTALL_FAILED,
        ERR_BUILD_FAILED,
        ERR_TEST_FAILED,
        ERR_PATH_ESCAPE,
        ERR_SYSTEM_INSTALL_BLOCKED,
    ):
        if code in errors:
            result.error_code = code
            break

    # HTML / static: never runtime-proven
    if kind_value == "html" or profile.runtime == "browser":
        validation["works"] = False
        validation["scaffold_only"] = True
        validation["runtime_proven"] = False
        validation["message"] = (
            validation.get("message")
            or "Static files delivered; not runtime-proven (open in a browser to verify)"
        )
        result.claimed_working = False
        result.validation = validation
        # Delivery can still be ok if scaffold succeeded
        if ERR_SCAFFOLD_FAILED not in errors and result.scaffold_ok:
            result.ok = True
        return result

    # Any hard pipeline failure forbids claimed_working
    hard = {
        ERR_TOOLCHAIN_UNAVAILABLE,
        ERR_UNSUPPORTED_ECOSYSTEM,
        ERR_INSTALL_FAILED,
        ERR_BUILD_FAILED,
        ERR_TEST_FAILED,
        ERR_SCAFFOLD_FAILED,
        ERR_PATH_ESCAPE,
        ERR_SYSTEM_INSTALL_BLOCKED,
    }
    if hard.intersection(errors):
        result.claimed_working = False
        validation["works"] = False
        if ERR_TOOLCHAIN_UNAVAILABLE in errors:
            validation["scaffold_only"] = True
            validation["error"] = ERR_TOOLCHAIN_UNAVAILABLE
        result.validation = validation
        result.ok = (
            result.scaffold_ok
            and ERR_SCAFFOLD_FAILED not in errors
            and ERR_TOOLCHAIN_UNAVAILABLE not in errors
            and ERR_UNSUPPORTED_ECOSYSTEM not in errors
            and ERR_INSTALL_FAILED not in errors
        )
        return result

    install_ok = (
        (not profile.install_cmd)
        or (not run_install)
        or (not profile.requires_install)
        or bool((result.install or {}).get("ok"))
    )
    build_ok = (not profile.build_cmd) or (not run_build) or bool((result.build or {}).get("ok"))
    test_ok = (not profile.test_cmd) or (not run_test) or bool((result.test or {}).get("ok"))

    structure_ok = bool(validation.get("structure_ok"))
    works = bool(structure_ok and install_ok and build_ok and test_ok and result.scaffold_ok)

    # Scaffold-only path: no install/build/test requested or required
    if works and run_install and profile.requires_install and profile.install_cmd:
        validation["scaffold_only"] = False
        validation["runtime_proven"] = True
    elif works and not profile.requires_install and not run_build and not run_test:
        # e.g. shell without proving make — still not "working" without a successful command
        if result.build or result.test:
            validation["scaffold_only"] = False
            validation["runtime_proven"] = True
        else:
            works = False
            validation["scaffold_only"] = True
            validation["runtime_proven"] = False
    else:
        if not works:
            validation["scaffold_only"] = True
            validation["runtime_proven"] = False

    validation["works"] = works
    validation["install_ok"] = install_ok
    validation["build_ok"] = build_ok
    validation["test_ok"] = test_ok
    if works:
        validation["message"] = "Structure + required commands succeeded"
    elif not validation.get("message"):
        validation["message"] = "Not proven working under fail-closed rules"

    result.validation = validation
    result.claimed_working = bool(works)
    # ok = pipeline delivered without hard failure (may still not be "working")
    result.ok = result.scaffold_ok and ERR_INSTALL_FAILED not in errors
    if not works:
        result.claimed_working = False
    return result


async def guarded_command_runner(
    command: str,
    ctx: dict,
    inner: Optional[CommandRunner] = None,
) -> dict:
    """Block system-level package installs; enforce project-scoped runner."""
    if is_system_level_install_command(command):
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": (
                "system_install_blocked: DevOS does not silently install system packages. "
                "Use project-local dependency commands only."
            ),
            "command": command,
            "error": ERR_SYSTEM_INSTALL_BLOCKED,
            "status": "failed",
        }
    # Path-ish escape in the command string itself
    if any(x in (command or "") for x in ("\x00",)):
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": "invalid command",
            "command": command,
            "error": ERR_PATH_ESCAPE,
            "status": "failed",
        }
    runner = inner or _default_runner
    result = await runner(command, ctx)
    if not isinstance(result, dict):
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "invalid_runner_result", "command": command}
    # Normalize path-escape signals from run_command_in_project
    err = str(result.get("stderr") or "")
    if "path separators refused" in err or "escapes projects directory" in err or "Invalid user_id" in err:
        result = dict(result)
        result["ok"] = False
        result["error"] = ERR_PATH_ESCAPE
    return result


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
    try:
        kind = detect_toolchain(request, explicit=toolchain)
        profile = get_profile(kind)
    except KeyError as e:
        result = BootstrapResult(ok=False, toolchain=str(toolchain or "unknown"), profile={})
        result.errors.append(ERR_UNSUPPORTED_ECOSYSTEM)
        result.error_code = ERR_UNSUPPORTED_ECOSYSTEM
        result.claimed_working = False
        result.validation = {
            "structure_ok": False,
            "works": False,
            "scaffold_only": True,
            "error": ERR_UNSUPPORTED_ECOSYSTEM,
            "message": str(e),
            "runtime_proven": False,
        }
        return result
    inner_runner = command_runner or _default_runner

    async def runner(cmd, c):
        return await guarded_command_runner(cmd, c, inner=inner_runner)

    ctx = {"user_id": user_id, "project_id": project_id, "timeout_s": 180}

    from execution.files import FileService
    fs = FileService(user_id, project_id)

    kind_value = profile.kind_value()
    result = BootstrapResult(
        ok=False,
        toolchain=kind_value,
        profile=profile.to_dict(),
    )

    # Runtime availability — never claim toolchain exists from files alone
    runtime = check_runtime_available(profile)
    result.validation = {"runtime": runtime}
    if not runtime.get("ok"):
        result.errors.append(ERR_TOOLCHAIN_UNAVAILABLE)
        result.error_code = ERR_TOOLCHAIN_UNAVAILABLE
        result.validation.update({
            "structure_ok": False,
            "works": False,
            "scaffold_only": True,
            "error": ERR_TOOLCHAIN_UNAVAILABLE,
            "message": runtime.get("message"),
            "missing_binaries": runtime.get("missing"),
            "runtime_proven": False,
        })
        result.claimed_working = False
        result.evidence_id = _evidence(
            "project.bootstrap", actor_id, "failed",
            {
                "stage": "runtime",
                "toolchain": kind_value,
                "missing": runtime.get("missing"),
                "tenant_id": tenant_id,
            },
        )
        return result

    written, sc_errors = scaffold_project(fs=fs, kind=kind, project_name=project_name)
    result.files_written = written
    result.artifact_refs = [f"file:{p}" for p in written]
    result.errors.extend(sc_errors)
    result.scaffold_ok = len(written) > 0 and not sc_errors
    if not result.scaffold_ok:
        result.errors.append(ERR_SCAFFOLD_FAILED)
        result.error_code = ERR_SCAFFOLD_FAILED
        result.claimed_working = False
        result.evidence_id = _evidence(
            "project.bootstrap", actor_id, "failed",
            {"stage": "scaffold", "toolchain": kind_value, "tenant_id": tenant_id},
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
                if (result.install or {}).get("error") == ERR_SYSTEM_INSTALL_BLOCKED:
                    result.errors.append(ERR_SYSTEM_INSTALL_BLOCKED)
                    result.error_code = ERR_SYSTEM_INSTALL_BLOCKED
                elif (result.install or {}).get("error") == ERR_PATH_ESCAPE:
                    result.errors.append(ERR_PATH_ESCAPE)
                    result.error_code = ERR_PATH_ESCAPE
                else:
                    result.errors.append(ERR_INSTALL_FAILED)
                    result.error_code = ERR_INSTALL_FAILED
                result.validation = validate_project(fs, profile, written)
                result.evidence_id = _evidence(
                    "project.bootstrap", actor_id, "failed",
                    {
                        "stage": "install",
                        "toolchain": kind_value,
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
    validation["runtime"] = (result.validation or {}).get("runtime") or check_runtime_available(profile)
    result.validation = validation

    # Map command outcomes into error list before fail-closed finalizer
    if run_build and profile.build_cmd and result.build and not result.build.get("ok"):
        if ERR_BUILD_FAILED not in result.errors:
            result.errors.append(ERR_BUILD_FAILED)
    if run_test and profile.test_cmd and result.test and not result.test.get("ok"):
        if ERR_TEST_FAILED not in result.errors:
            result.errors.append(ERR_TEST_FAILED)
    if result.install and result.install.get("error") == ERR_SYSTEM_INSTALL_BLOCKED:
        if ERR_SYSTEM_INSTALL_BLOCKED not in result.errors:
            result.errors.append(ERR_SYSTEM_INSTALL_BLOCKED)
    if result.install and result.install.get("error") == ERR_PATH_ESCAPE:
        if ERR_PATH_ESCAPE not in result.errors:
            result.errors.append(ERR_PATH_ESCAPE)

    result = apply_fail_closed_claims(
        result,
        kind_value=kind_value,
        profile=profile,
        run_install=run_install,
        run_build=run_build,
        run_test=run_test,
    )

    result.evidence_id = _evidence(
        "project.bootstrap",
        actor_id,
        "success" if result.ok else "failed",
        {
            "toolchain": kind_value,
            "files": written,
            "claimed_working": result.claimed_working,
            "errors": result.errors,
            "error_code": result.error_code,
            "tenant_id": tenant_id,
        },
    )
    return result
