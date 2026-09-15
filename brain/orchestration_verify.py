"""
Artifact verification for orchestration nodes.

Execution success ≠ completion. Verification inspects workspace/evidence.
Not a second verification framework — uses existing FileService when available.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("devos.orchestration_verify")


def _is_website_goal(goal: str) -> bool:
    g = (goal or "").lower()
    return any(
        k in g
        for k in (
            "website",
            "web site",
            "landing page",
            "landing",
            "homepage",
            "home page",
            "one-page",
            "one page",
            "site for",
            "page for",
            "build a site",
            "make a site",
            "create a site",
        )
    )


async def validate_website_artifacts(
    *,
    user_id: str,
    workspace_id: str,
    goal: str = "",
) -> dict[str, Any]:
    """
    Post-agent website validation. Authoritative FileService inspection only.
    """
    result: dict[str, Any] = {
        "valid": False,
        "status": "invalid",
        "warnings": [],
        "files_checked": [],
        "entry_point": None,
        "structure": None,
        "errors": [],
    }
    try:
        from execution.files import FileService
        fs = FileService(user_id, workspace_id or "default")
    except Exception as e:
        result["errors"].append(f"fileservice:{e}")
        result["status"] = "invalid"
        return result

    def _exists(path: str) -> bool:
        try:
            if hasattr(fs, "exists"):
                return bool(fs.exists(path))
            if hasattr(fs, "read"):
                fs.read(path)
                return True
        except Exception:
            return False
        return False

    def _read_text(path: str) -> str:
        try:
            if hasattr(fs, "read_text"):
                return fs.read_text(path) or ""
            data = fs.read(path)
            if isinstance(data, bytes):
                return data.decode("utf-8", errors="replace")
            if isinstance(data, dict):
                return str(data.get("content") or data.get("text") or "")
            return str(data or "")
        except Exception:
            return ""

    # Static HTML
    static_entries = ["index.html", "index.htm", "public/index.html"]
    # Framework structures
    framework_markers = {
        "vite": ["vite.config.js", "vite.config.ts", "src/main.jsx", "src/main.tsx"],
        "react": ["src/App.jsx", "src/App.tsx", "src/main.jsx", "src/main.tsx"],
        "next": ["next.config.js", "next.config.mjs", "app/page.tsx", "pages/index.tsx"],
        "package": ["package.json"],
    }

    entry = None
    for p in static_entries:
        if _exists(p):
            entry = p
            result["structure"] = "static"
            break

    if not entry:
        for kind, paths in framework_markers.items():
            hits = [p for p in paths if _exists(p)]
            if hits:
                result["structure"] = kind
                entry = hits[0]
                result["files_checked"].extend(hits)
                break

    if not entry:
        result["errors"].append("no_entry_file")
        result["status"] = "invalid"
        return result

    result["entry_point"] = entry
    result["files_checked"].append(entry)

    if entry.endswith((".html", ".htm")):
        html = _read_text(entry)
        if not html.strip():
            result["errors"].append("empty_html")
            result["status"] = "invalid"
            return result
        low = html.lower()
        if "<html" not in low and "<!doctype" not in low and "<body" not in low:
            result["warnings"].append("html_missing_document_structure")
        # Local asset references
        for rel in re.findall(r'(?:href|src)=["\']([^"\']+)["\']', html, flags=re.I):
            if rel.startswith(("http://", "https://", "//", "data:", "#", "mailto:")):
                continue
            clean = rel.split("?")[0].split("#")[0].lstrip("./")
            if not clean or clean.endswith("/"):
                continue
            result["files_checked"].append(clean)
            if not _exists(clean):
                result["warnings"].append(f"missing_asset:{clean}")

    result["valid"] = len(result["errors"]) == 0
    result["status"] = "valid" if result["valid"] else "invalid"
    if result["warnings"] and result["valid"]:
        result["status"] = "valid_with_warnings"
    return result


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
        "verified": False,
    }
    goal_l = (goal or "").lower()
    expected = list(expected_outputs or [])
    needs_site = _is_website_goal(goal) or any(
        k in goal_l for k in ("website", "page", "landing", "site", "shoe")
    )

    try:
        from execution.files import FileService
        fs = FileService(user_id, workspace_id or "default")
    except Exception as e:
        evidence["errors"].append(f"fileservice:{e}")
        if files_changed:
            evidence["checks"].append({
                "name": "agent_files_changed",
                "ok": False,
                "count": len(files_changed),
                "note": "unconfirmed without FileService",
            })
            evidence["weak"] = True
        evidence["errors"].append("verification_incomplete: FileService unavailable")
        evidence["criteria"] = [c.get("name") for c in evidence.get("checks") or []]
        evidence["failed_criteria"] = [
            c.get("name") for c in (evidence.get("checks") or []) if not c.get("ok")
        ]
        evidence["verifier"] = "orchestration_verify.verify_workspace_artifacts"
        return evidence

    def _exists(path: str) -> bool:
        try:
            if hasattr(fs, "exists"):
                return bool(fs.exists(path))
            if hasattr(fs, "read"):
                try:
                    fs.read(path)
                    return True
                except Exception:
                    return False
        except Exception:
            return False
        return False

    candidates = [
        "index.html", "index.htm", "public/index.html",
        "src/App.jsx", "src/App.tsx", "src/main.tsx", "src/main.jsx",
        "app/page.tsx", "pages/index.tsx",
        "app.py", "main.py", "README.md", "package.json",
        "style.css", "styles.css", "script.js",
    ]
    if "shoe" in goal_l:
        candidates.extend(["shoes.html", "src/pages/Shoes.jsx"])
    for e in expected:
        if isinstance(e, str) and e.strip():
            candidates.append(e.strip())

    found = []
    for path in list(dict.fromkeys(candidates)):
        if _exists(path):
            found.append(path)

    evidence["files_found"] = found
    evidence["checks"].append({"name": "entry_files", "ok": len(found) > 0, "found": found})

    if files_changed:
        evidence["checks"].append({
            "name": "execution_files_changed",
            "ok": len(files_changed) > 0,
            "count": len(files_changed),
        })

    if needs_site:
        site = await validate_website_artifacts(
            user_id=user_id,
            workspace_id=workspace_id,
            goal=goal,
        )
        evidence["website_validation"] = site
        evidence["checks"].append({
            "name": "site_structure",
            "ok": bool(site.get("valid")),
            "entry_point": site.get("entry_point"),
            "structure": site.get("structure"),
            "warnings": site.get("warnings") or [],
        })
        evidence["passed"] = bool(site.get("valid"))
        if not evidence["passed"] and files_changed:
            evidence["weak"] = True
            evidence["checks"].append({
                "name": "agent_files_changed_unconfirmed",
                "ok": False,
                "count": len(files_changed),
            })
        if site.get("errors"):
            evidence["errors"].extend(list(site["errors"]))
    else:
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
        matched = [e for e in expected if any(str(e) in f or f in str(e) for f in found)]
        evidence["checks"].append({
            "name": "expected_outputs",
            "ok": bool(matched) or not needs_site,
            "matched": matched,
        })

    evidence["verified"] = bool(evidence.get("passed"))
    evidence["criteria"] = [c.get("name") for c in evidence.get("checks") or []]
    evidence["failed_criteria"] = [
        c.get("name") for c in (evidence.get("checks") or []) if not c.get("ok")
    ]
    evidence["verifier"] = "orchestration_verify.verify_workspace_artifacts"
    return evidence
