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
        # Agent-reported files_changed alone is WEAK — not authoritative success.
        # Without FileService we cannot confirm artifacts on disk.
        if files_changed:
            evidence["checks"].append({
                "name": "agent_files_changed",
                "ok": True,
                "count": len(files_changed),
                "note": "unconfirmed without FileService",
            })
            evidence["weak"] = True
            evidence["passed"] = False  # unknown, not success
            evidence["errors"].append("verification_incomplete: FileService unavailable")
        evidence["verified"] = bool(evidence.get("passed"))
    evidence["criteria"] = [c.get("name") for c in evidence.get("checks") or []]
    evidence["failed_criteria"] = [
        c.get("name") for c in (evidence.get("checks") or []) if not c.get("ok")
    ]
    evidence["verifier"] = "orchestration_verify.verify_workspace_artifacts"
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

    # Website-like goals need at least one entry file or package.json ON DISK
    needs_site = any(k in goal_l for k in ("website", "page", "landing", "site", "shoe"))
    if needs_site:
        ok = any(p.endswith((".html", ".htm", ".jsx", ".tsx")) for p in found) or "package.json" in found
        evidence["checks"].append({"name": "site_structure", "ok": ok})
        # Agent-reported changes without found files are not sufficient
        evidence["passed"] = bool(ok)
        if not ok and files_changed:
            evidence["weak"] = True
            evidence["checks"].append({
                "name": "agent_files_changed_unconfirmed",
                "ok": False,
                "count": len(files_changed),
            })
    else:
        # generic: require on-disk evidence; files_changed alone is weak
        if len(found) > 0:
            evidence["passed"] = True
        elif files_changed:
            evidence["weak"] = True
            evidence["passed"] = False
            evidence["checks"].append({
                "name": "agent_files_changed_unconfirmed",
                "ok": False,
                "count": len(files_changed),
            })
        else:
            evidence["passed"] = False

    if expected:
        # soft match against found names
        matched = [e for e in expected if any(e in f or f in str(e) for f in found)]
        evidence["checks"].append({"name": "expected_outputs", "ok": bool(matched) or not needs_site, "matched": matched})

    return evidence
