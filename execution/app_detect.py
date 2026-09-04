"""Application / framework detection from workspace metadata (not guesses)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from execution.files import FileService


def _read_text(fs: FileService, rel: str) -> Optional[str]:
    try:
        p = fs._resolve(rel)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _exists(fs: FileService, rel: str) -> bool:
    try:
        return fs._resolve(rel).exists()
    except Exception:
        return False


def detect_package_manager(fs: FileService) -> Optional[str]:
    if _exists(fs, "pnpm-lock.yaml"):
        return "pnpm"
    if _exists(fs, "yarn.lock"):
        return "yarn"
    if _exists(fs, "bun.lockb") or _exists(fs, "bun.lock"):
        return "bun"
    if _exists(fs, "package-lock.json") or _exists(fs, "package.json"):
        return "npm"
    return None


def detect_application(fs: FileService) -> dict[str, Any]:
    """Inspect project metadata. Does not run package managers."""
    result: dict[str, Any] = {
        "kind": "UNKNOWN_APP",
        "framework": None,
        "package_manager": detect_package_manager(fs),
        "package_json": None,
        "scripts": {},
        "has_app_dir": _exists(fs, "app"),
        "has_pages_dir": _exists(fs, "pages"),
        "has_src": _exists(fs, "src"),
        "has_public": _exists(fs, "public"),
        "entrypoint_candidates": [],
        "confidence": 0.0,
    }
    # Static site
    if _exists(fs, "index.html") and not _exists(fs, "package.json"):
        result.update(kind="STATIC_SITE", framework="static", confidence=0.9,
                      entrypoint_candidates=["index.html"])
        return result

    pkg_raw = _read_text(fs, "package.json")
    if not pkg_raw:
        if _exists(fs, "requirements.txt") or _exists(fs, "pyproject.toml"):
            result.update(kind="PYTHON_APP", framework="python", confidence=0.6)
        return result

    try:
        pkg = json.loads(pkg_raw)
    except json.JSONDecodeError:
        result["kind"] = "NODE_APP"
        result["confidence"] = 0.4
        return result

    result["package_json"] = {
        "name": pkg.get("name"),
        "private": pkg.get("private"),
        "engines": pkg.get("engines"),
        "packageManager": pkg.get("packageManager"),
    }
    scripts = pkg.get("scripts") or {}
    result["scripts"] = {k: scripts[k] for k in scripts if k in (
        "dev", "build", "start", "preview", "lint", "test"
    )}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    deps_l = {k.lower(): v for k, v in deps.items()}

    if "next" in deps_l or _exists(fs, "next.config.js") or _exists(fs, "next.config.mjs") or _exists(fs, "next.config.ts"):
        result.update(
            kind="NEXTJS_APP",
            framework="nextjs",
            confidence=0.95,
            router="app" if result["has_app_dir"] else ("pages" if result["has_pages_dir"] else "unknown"),
        )
        return result
    if "vite" in deps_l or any(_exists(fs, c) for c in ("vite.config.js", "vite.config.ts", "vite.config.mjs")):
        result.update(kind="VITE_APP", framework="vite", confidence=0.9)
        return result
    if "react" in deps_l and ("react-scripts" in deps_l or "react-dom" in deps_l):
        result.update(kind="REACT_APP", framework="react", confidence=0.75)
        return result
    if deps:
        result.update(kind="NODE_APP", framework="node", confidence=0.6)
        return result
    if _exists(fs, "index.html"):
        result.update(kind="STATIC_SITE", framework="static", confidence=0.7,
                      entrypoint_candidates=["index.html"])
    return result
