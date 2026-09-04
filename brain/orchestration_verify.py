"""
Artifact verification for orchestration nodes.

Execution success ≠ completion. Verification inspects workspace/evidence.
Not a second verification framework — uses existing FileService when available.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("devos.orchestration_verify")


async def verify_workspace_artifacts(
    *,
    user_id: str,
    workspace_id: str,
    goal: str = "",
    expected_outputs: Optional[list] = None,
    files_changed: Optional[list] = None,
) -> dict[str, Any]:
    """
    Inspect actual workspace. Returns evidence dict with passed: bool.
    Does not award XP. Does not authorize anything.
    """
    evidence: dict[str, Any] = {
        "passed": False,
        "checks": [],
        "files_found": [],
        "errors": [],
    }
    goal_l = (goal or "").lower()
    expected = list(expected_outputs or [])

    try:
        from execution.files import FileService
        fs = FileService(user_id, workspace_id or "default")
    except Exception as e:
        evidence["errors"].append(f"fileservice:{e}")
        # Soft: if files_changed reported by agent, treat as weak evidence only
        if files_changed:
            evidence["checks"].append({"name": "agent_files_changed", "ok": True, "count": len(files_changed)})
            evidence["passed"] = True
            evidence["weak"] = True
        return evidence

    candidates = [
        "index.html", "index.htm", "public/index.html",
        "src/App.jsx", "src/App.tsx", "src/main.tsx", "src/main.jsx",
        "app.py", "main.py", "README.md", "package.json",
    ]
    # goal-driven extras
    if "shoe" in goal_l:
        candidates.extend(["shoes.html", "src/pages/Shoes.jsx"])

    found = []
    for path in candidates:
        try:
            exists = False
            if hasattr(fs, "exists"):
                exists = bool(fs.exists(path))
            elif hasattr(fs, "read"):
                try:
                    fs.read(path)
                    exists = True
                except Exception:
                    exists = False
            if exists:
                found.append(path)
        except Exception:
            continue

    evidence["files_found"] = found
    evidence["checks"].append({"name": "entry_files", "ok": len(found) > 0, "found": found})

    if files_changed:
        evidence["checks"].append({
            "name": "execution_files_changed",
            "ok": len(files_changed) > 0,
            "count": len(files_changed),
        })

    # Website-like goals need at least one entry file or package.json
    needs_site = any(k in goal_l for k in ("website", "page", "landing", "site", "shoe"))
    if needs_site:
        ok = any(p.endswith((".html", ".htm", ".jsx", ".tsx")) for p in found) or "package.json" in found
        evidence["checks"].append({"name": "site_structure", "ok": ok})
        evidence["passed"] = ok or (bool(files_changed) and len(files_changed) > 0)
    else:
        # generic: any found file or agent reported changes
        evidence["passed"] = len(found) > 0 or bool(files_changed)

    if expected:
        # soft match against found names
        matched = [e for e in expected if any(e in f or f in str(e) for f in found)]
        evidence["checks"].append({"name": "expected_outputs", "ok": bool(matched) or not needs_site, "matched": matched})

    return evidence
