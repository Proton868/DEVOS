"""
Governed dynamic tool/skill acquisition for coding agents.

When an agent needs a capability that is not registered, it may *propose*
a skill definition. Installation only proceeds through this module:

  detect missing → propose → validate schema → security/governance checks
  → HITL if privileged → register capability + tool → isolation test
  → evidence/audit

Layers stay separate:
  skill definition  ≠  executable capability  ≠  authorization
  ≠  tool execution  ≠  evidence

Agents cannot silently grant themselves unrestricted permissions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("devos.skill_acquisition")

# ── Forbidden patterns in generated handlers / definitions ───────────────────

_FORBIDDEN_SLUG = re.compile(r"[^a-z0-9._:-]", re.I)
_DANGEROUS_NAME_BITS = (
    "sudo", "rm_rf", "format_disk", "raw_shell", "unrestricted",
    "bypass_ucip", "disable_auth", "exfiltrate", "drop_table",
)
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 2000
_MAX_SCHEMA_KEYS = 40


class SkillRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    INSTALLED = "installed"
    TEST_FAILED = "test_failed"


@dataclass
class SkillDefinition:
    """Declarative skill/tool proposal (not yet executable)."""

    name: str
    description: str
    capability_slug: str
    input_schema: dict = field(default_factory=dict)
    risk: SkillRisk = SkillRisk.MEDIUM
    side_effect: str = "none"  # none | workspace | network | system
    reason: str = ""  # why the agent needs this
    requested_by: str = ""  # agent / persona id
    tenant_id: str = "default"
    handler_kind: str = "echo"  # only safe built-in kinds allowed
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["risk"] = self.risk.value if isinstance(self.risk, SkillRisk) else str(self.risk)
        return d


@dataclass
class SkillProposal:
    proposal_id: str
    definition: SkillDefinition
    status: ProposalStatus = ProposalStatus.DRAFT
    validation_errors: list[str] = field(default_factory=list)
    security_notes: list[str] = field(default_factory=list)
    hitl_request_id: Optional[str] = None
    installed_tool: Optional[str] = None
    installed_capability: Optional[str] = None
    isolation_test: Optional[dict] = None
    evidence_ids: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "definition": self.definition.to_dict(),
            "status": self.status.value,
            "validation_errors": list(self.validation_errors),
            "security_notes": list(self.security_notes),
            "hitl_request_id": self.hitl_request_id,
            "installed_tool": self.installed_tool,
            "installed_capability": self.installed_capability,
            "isolation_test": self.isolation_test,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# In-process store (tests + single-node); durable evidence is separate.
_PROPOSALS: dict[str, SkillProposal] = {}
_INSTALLED: dict[str, str] = {}  # tool_name → proposal_id
_DYNAMIC_HANDLERS: dict[str, str] = {}  # tool_name → safe handler_kind

# Safe handler kinds only — no arbitrary code execution from model output.
# Implementations are fixed Python functions registered in-process. Agents can
# *bind* a tool name to a kind; they cannot inject new implementation code.
_SAFE_HANDLERS: dict[str, Callable[..., dict]] = {}

# Catalog metadata for production-useful kinds (still sandbox-safe).
HANDLER_CATALOG: dict[str, dict] = {
    "echo": {
        "category": "utility",
        "summary": "Echo arguments (regression / smoke)",
        "max_risk": "low",
        "side_effect": "none",
    },
    "json_validate": {
        "category": "code_analysis",
        "summary": "Parse/validate JSON text or object",
        "max_risk": "low",
        "side_effect": "none",
    },
    "hash_text": {
        "category": "utility",
        "summary": "SHA-256 hash of provided text (no secret store access)",
        "max_risk": "low",
        "side_effect": "none",
    },
    "text_transform": {
        "category": "file_transform",
        "summary": "Safe text transforms: upper|lower|strip|slugify|reverse",
        "max_risk": "low",
        "side_effect": "none",
    },
    "line_stats": {
        "category": "code_analysis",
        "summary": "Line/word/char counts for provided text",
        "max_risk": "low",
        "side_effect": "none",
    },
    "diff_summary": {
        "category": "code_analysis",
        "summary": "Summarize differences between two text blobs (no FS)",
        "max_risk": "low",
        "side_effect": "none",
    },
    "json_path_get": {
        "category": "code_analysis",
        "summary": "Read a simple dotted path from JSON (no code eval)",
        "max_risk": "low",
        "side_effect": "none",
    },
    "regex_extract": {
        "category": "code_analysis",
        "summary": "Extract limited regex matches from text (bounded)",
        "max_risk": "low",
        "side_effect": "none",
    },
    "path_normalize": {
        "category": "project_toolchain",
        "summary": "Normalize a relative path string; reject traversal",
        "max_risk": "low",
        "side_effect": "none",
    },
    "extension_tally": {
        "category": "project_toolchain",
        "summary": "Count file extensions from a list of path strings",
        "max_risk": "low",
        "side_effect": "none",
    },
    "parse_pytest_summary": {
        "category": "test_build_adapter",
        "summary": "Parse pytest output for pass/fail/error counts",
        "max_risk": "low",
        "side_effect": "none",
    },
    "parse_compiler_errors": {
        "category": "test_build_adapter",
        "summary": "Parse common tsc/eslint/python traceback lines from text",
        "max_risk": "low",
        "side_effect": "none",
    },
    "markdown_outline": {
        "category": "code_analysis",
        "summary": "Extract markdown heading outline from text",
        "max_risk": "low",
        "side_effect": "none",
    },
    "csv_preview": {
        "category": "file_transform",
        "summary": "Preview first N rows of CSV text",
        "max_risk": "low",
        "side_effect": "none",
    },
    "semver_compare": {
        "category": "project_toolchain",
        "summary": "Compare two semver-like version strings",
        "max_risk": "low",
        "side_effect": "none",
    },
}


def _register_safe_handler(kind: str, fn: Callable[..., dict]) -> None:
    _SAFE_HANDLERS[kind] = fn


def _bound_text(val: Any, limit: int = 200_000) -> str:
    s = str(val if val is not None else "")
    if len(s) > limit:
        return s[:limit]
    return s


def _handler_echo(args: dict, **_ctx) -> dict:
    return {"ok": True, "echo": args, "handler": "echo"}


def _handler_json_validate(args: dict, **_ctx) -> dict:
    payload = args.get("payload")
    if payload is None:
        return {"ok": False, "error": "payload required"}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as e:
            return {"ok": False, "error": f"invalid json: {type(e).__name__}"}
    return {"ok": True, "valid": True, "type": type(payload).__name__}


def _handler_hash_text(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"ok": True, "sha256": digest, "length": len(text)}


def _handler_text_transform(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or "")
    op = str(args.get("op") or "strip").lower().strip()
    allowed = {
        "upper": lambda s: s.upper(),
        "lower": lambda s: s.lower(),
        "strip": lambda s: s.strip(),
        "reverse": lambda s: s[::-1],
        "slugify": lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:200],
    }
    if op not in allowed:
        return {"ok": False, "error": f"op must be one of {sorted(allowed)}"}
    return {"ok": True, "op": op, "result": allowed[op](text), "handler": "text_transform"}


def _handler_line_stats(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or "")
    lines = text.splitlines()
    words = text.split()
    return {
        "ok": True,
        "lines": len(lines),
        "words": len(words),
        "chars": len(text),
        "non_empty_lines": sum(1 for ln in lines if ln.strip()),
        "handler": "line_stats",
    }


def _handler_diff_summary(args: dict, **_ctx) -> dict:
    a = _bound_text(args.get("before") or args.get("a") or "", 100_000)
    b = _bound_text(args.get("after") or args.get("b") or "", 100_000)
    la, lb = a.splitlines(), b.splitlines()
    first_diff = None
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            first_diff = i + 1
            break
    if first_diff is None and len(la) != len(lb):
        first_diff = min(len(la), len(lb)) + 1
    return {
        "ok": True,
        "before_lines": len(la),
        "after_lines": len(lb),
        "identical": a == b,
        "first_diff_line": first_diff,
        "handler": "diff_summary",
    }


def _handler_json_path_get(args: dict, **_ctx) -> dict:
    payload = args.get("payload")
    path = str(args.get("path") or "").strip()
    if payload is None:
        return {"ok": False, "error": "payload required"}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {"ok": False, "error": "invalid_json"}
    if not path or not re.match(r"^[A-Za-z0-9_.\[\]-]+$", path) or ".." in path:
        return {"ok": False, "error": "invalid_path"}
    cur: Any = payload
    for part in path.replace("[", ".").replace("]", "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if idx < 0 or idx >= len(cur):
                return {"ok": False, "error": "index_out_of_range"}
            cur = cur[idx]
        else:
            return {"ok": False, "error": "path_not_found"}
    # Never return huge nested objects
    if isinstance(cur, (dict, list)):
        try:
            enc = json.dumps(cur, default=str)
            if len(enc) > 20_000:
                return {"ok": True, "truncated": True, "type": type(cur).__name__, "size": len(enc)}
        except Exception:
            return {"ok": False, "error": "unserializable"}
    return {"ok": True, "value": cur, "type": type(cur).__name__, "handler": "json_path_get"}


def _handler_regex_extract(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or "", 50_000)
    pattern = str(args.get("pattern") or "")
    if not pattern or len(pattern) > 200:
        return {"ok": False, "error": "pattern required (≤200 chars)"}
    # Refuse nested quantifiers that commonly enable ReDoS
    if re.search(r"(\+|\*|\{\d+,?\d*\})\1", pattern) or "(?!" in pattern or "(?<" in pattern:
        return {"ok": False, "error": "pattern_not_allowed"}
    try:
        rx = re.compile(pattern)
    except re.error:
        return {"ok": False, "error": "invalid_regex"}
    matches = []
    for m in rx.finditer(text):
        matches.append(m.group(0)[:500])
        if len(matches) >= 50:
            break
    return {"ok": True, "count": len(matches), "matches": matches, "handler": "regex_extract"}


def _handler_path_normalize(args: dict, **_ctx) -> dict:
    raw = str(args.get("path") or args.get("rel") or "").strip().replace("\\", "/")
    if not raw:
        return {"ok": False, "error": "path required"}
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or "\x00" in raw:
        return {"ok": False, "error": "absolute_or_null_path_rejected"}
    parts = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            return {"ok": False, "error": "path_traversal_rejected"}
        if seg.startswith("~"):
            return {"ok": False, "error": "home_path_rejected"}
        parts.append(seg)
    norm = "/".join(parts)
    return {"ok": True, "path": norm, "handler": "path_normalize"}


def _handler_extension_tally(args: dict, **_ctx) -> dict:
    paths = args.get("paths") or args.get("files") or []
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.splitlines() if p.strip()]
    if not isinstance(paths, list):
        return {"ok": False, "error": "paths must be a list"}
    tallies: dict[str, int] = {}
    for p in paths[:5000]:
        s = str(p).replace("\\", "/")
        if ".." in s.split("/"):
            continue
        if "." not in s.rsplit("/", 1)[-1]:
            ext = "(none)"
        else:
            ext = s.rsplit(".", 1)[-1].lower()[:20]
        tallies[ext] = tallies.get(ext, 0) + 1
    return {"ok": True, "counts": tallies, "total": sum(tallies.values()), "handler": "extension_tally"}


def _handler_parse_pytest_summary(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or args.get("stdout") or "", 100_000)
    passed = failed = errors = skipped = 0
    m = re.search(r"(\d+)\s+passed", text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", text)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+error", text)
    if m:
        errors = int(m.group(1))
    m = re.search(r"(\d+)\s+skipped", text)
    if m:
        skipped = int(m.group(1))
    ok = failed == 0 and errors == 0 and ("passed" in text or passed > 0)
    return {
        "ok": True,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "tests_ok": ok and failed == 0 and errors == 0,
        "handler": "parse_pytest_summary",
    }


def _handler_parse_compiler_errors(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or args.get("stderr") or "", 100_000)
    findings = []
    # TypeScript/ESLint style: path(line,col): error TS####: message
    for m in re.finditer(
        r"(?P<file>[\w./\\-]+)\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<sev>error|warning)\s+(?P<code>\w+):\s*(?P<msg>.+)",
        text,
    ):
        findings.append({
            "file": m.group("file")[:200],
            "line": int(m.group("line")),
            "severity": m.group("sev"),
            "code": m.group("code"),
            "message": m.group("msg").strip()[:300],
        })
        if len(findings) >= 50:
            break
    # Python traceback: File "x.py", line N
    if len(findings) < 50:
        for m in re.finditer(r'File "([^"]+)", line (\d+)', text):
            findings.append({
                "file": m.group(1)[:200],
                "line": int(m.group(2)),
                "severity": "error",
                "code": "python",
                "message": "traceback",
            })
            if len(findings) >= 50:
                break
    return {
        "ok": True,
        "count": len(findings),
        "findings": findings,
        "handler": "parse_compiler_errors",
    }


def _handler_markdown_outline(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or "", 100_000)
    headings = []
    for m in re.finditer(r"^(#{1,6})\s+(.+)$", text, re.M):
        headings.append({"level": len(m.group(1)), "title": m.group(2).strip()[:200]})
        if len(headings) >= 100:
            break
    return {"ok": True, "headings": headings, "count": len(headings), "handler": "markdown_outline"}


def _handler_csv_preview(args: dict, **_ctx) -> dict:
    text = _bound_text(args.get("text") or "", 50_000)
    max_rows = min(int(args.get("max_rows") or 10), 50)
    rows = []
    for i, line in enumerate(text.splitlines()):
        if i >= max_rows:
            break
        # Simple CSV split (no RFC4180 edge cases — preview only)
        rows.append([c.strip()[:200] for c in line.split(",")][:30])
    return {"ok": True, "rows": rows, "row_count": len(rows), "handler": "csv_preview"}


def _handler_semver_compare(args: dict, **_ctx) -> dict:
    def parts(v: str):
        v = re.sub(r"^[^0-9]*", "", str(v).strip())
        nums = []
        for bit in re.split(r"[.+-]", v):
            if bit.isdigit():
                nums.append(int(bit))
            else:
                break
        return nums or [0]

    a, b = str(args.get("a") or ""), str(args.get("b") or "")
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    cmp = 0
    for x, y in zip(pa, pb):
        if x < y:
            cmp = -1
            break
        if x > y:
            cmp = 1
            break
    return {"ok": True, "a": a, "b": b, "cmp": cmp, "handler": "semver_compare"}


_register_safe_handler("echo", _handler_echo)
_register_safe_handler("json_validate", _handler_json_validate)
_register_safe_handler("hash_text", _handler_hash_text)
_register_safe_handler("text_transform", _handler_text_transform)
_register_safe_handler("line_stats", _handler_line_stats)
_register_safe_handler("diff_summary", _handler_diff_summary)
_register_safe_handler("json_path_get", _handler_json_path_get)
_register_safe_handler("regex_extract", _handler_regex_extract)
_register_safe_handler("path_normalize", _handler_path_normalize)
_register_safe_handler("extension_tally", _handler_extension_tally)
_register_safe_handler("parse_pytest_summary", _handler_parse_pytest_summary)
_register_safe_handler("parse_compiler_errors", _handler_parse_compiler_errors)
_register_safe_handler("markdown_outline", _handler_markdown_outline)
_register_safe_handler("csv_preview", _handler_csv_preview)
_register_safe_handler("semver_compare", _handler_semver_compare)


def list_safe_handler_kinds() -> list[dict]:
    """Catalog of installable handler kinds (no unrestricted ops)."""
    out = []
    for kind, meta in HANDLER_CATALOG.items():
        if kind not in _SAFE_HANDLERS:
            continue
        out.append({
            "handler_kind": kind,
            "category": meta.get("category"),
            "summary": meta.get("summary"),
            "max_risk": meta.get("max_risk", "low"),
            "side_effect": meta.get("side_effect", "none"),
            "executable": True,
        })
    return out


def suggest_handler_for_need(need: str) -> dict:
    """Map a free-text need to a safe handler_kind or honest failure."""
    n = (need or "").lower()
    mapping = [
        (("json", "parse json", "validate json"), "json_validate"),
        (("hash", "checksum", "sha256"), "hash_text"),
        (("slug", "uppercase", "lowercase", "transform text"), "text_transform"),
        (("line count", "word count", "stats"), "line_stats"),
        (("diff", "compare text", "what changed"), "diff_summary"),
        (("json path", "get field", "nested key"), "json_path_get"),
        (("regex", "extract", "match pattern"), "regex_extract"),
        (("path", "normalize path", "relative path"), "path_normalize"),
        (("extension", "file types", "project files"), "extension_tally"),
        (("pytest", "test results", "test summary"), "parse_pytest_summary"),
        (("tsc", "compiler", "eslint", "traceback"), "parse_compiler_errors"),
        (("markdown", "outline", "headings"), "markdown_outline"),
        (("csv", "spreadsheet preview"), "csv_preview"),
        (("version", "semver"), "semver_compare"),
    ]
    for keys, kind in mapping:
        if any(k in n for k in keys):
            return {
                "ok": True,
                "handler_kind": kind,
                "catalog": HANDLER_CATALOG.get(kind),
                "note": "Install via propose_skill with this handler_kind; UCIP still governs execution.",
            }
    # Explicitly refuse dangerous needs
    for bad in ("shell", "bash", "rm -rf", "network", "http", "deploy", "ssh", "credential", "database", "sudo"):
        if bad in n:
            return {
                "ok": False,
                "error": "capability_not_generatable",
                "reason": (
                    f"Requested capability involves '{bad}' which cannot be auto-generated. "
                    "Use existing governed tools (run_command/run_tests) under UCIP, or request human approval."
                ),
            }
    return {
        "ok": False,
        "error": "no_safe_handler_match",
        "reason": "No safe handler_kind matches this need. Available: " + ", ".join(sorted(_SAFE_HANDLERS)),
        "available": list_safe_handler_kinds(),
    }


def detect_missing_capability(name_or_slug: str) -> dict:
    """Return whether a tool/capability is missing and a short explanation."""
    name = (name_or_slug or "").strip()
    out = {
        "name": name,
        "missing": True,
        "as_tool": False,
        "as_capability": False,
        "reason": "",
    }
    if not name:
        out["reason"] = "empty capability name"
        return out

    try:
        from brain.agent_tools import get_agent_tool

        tool = get_agent_tool(name)
        if tool is not None:
            out["missing"] = False
            out["as_tool"] = True
            out["reason"] = f"tool '{name}' is already registered"
            return out
    except Exception:
        pass

    try:
        from governance.capability_registry import get_registry

        reg = get_registry()
        slug = name if name.startswith("ucip:") else f"ucip:skill.{name}"
        cap = reg.get(slug) or reg.get(name)
        if cap is not None:
            out["missing"] = False
            out["as_capability"] = True
            out["reason"] = f"capability '{cap.slug}' is registered"
            return out
    except Exception:
        pass

    if name in _INSTALLED:
        out["missing"] = False
        out["reason"] = f"dynamically installed via proposal {_INSTALLED[name]}"
        return out

    out["reason"] = (
        f"No registered agent tool or UCIP capability named '{name}'. "
        "A skill proposal is required before use."
    )
    return out


def validate_skill_definition(defn: SkillDefinition) -> list[str]:
    """Schema + policy validation. Returns list of errors (empty = ok)."""
    errors: list[str] = []
    name = (defn.name or "").strip()
    if not name or len(name) > _MAX_NAME_LEN:
        errors.append("name required and must be ≤ 64 chars")
    if not re.match(r"^[a-z][a-z0-9_]*$", name or ""):
        errors.append("name must be snake_case starting with a letter")
    for bit in _DANGEROUS_NAME_BITS:
        if bit in name.lower():
            errors.append(f"name contains forbidden token '{bit}'")

    desc = (defn.description or "").strip()
    if not desc:
        errors.append("description required")
    if len(desc) > _MAX_DESC_LEN:
        errors.append("description too long")

    slug = (defn.capability_slug or "").strip()
    if not slug.startswith("ucip:skill."):
        errors.append("capability_slug must start with 'ucip:skill.'")
    if _FORBIDDEN_SLUG.search(slug.replace("ucip:skill.", "")):
        errors.append("capability_slug has invalid characters")

    schema = defn.input_schema or {}
    if schema and not isinstance(schema, dict):
        errors.append("input_schema must be an object")
    elif isinstance(schema, dict):
        props = schema.get("properties") or {}
        if len(props) > _MAX_SCHEMA_KEYS:
            errors.append("input_schema has too many properties")

    risk = defn.risk if isinstance(defn.risk, SkillRisk) else SkillRisk(str(defn.risk))
    side = (defn.side_effect or "none").lower()
    if side not in ("none", "workspace", "network", "system"):
        errors.append("side_effect must be none|workspace|network|system")
    if side in ("network", "system") and risk in (SkillRisk.LOW, SkillRisk.MEDIUM):
        errors.append("network/system side_effect requires risk high or critical")

    kind = (defn.handler_kind or "").strip()
    if kind not in _SAFE_HANDLERS:
        errors.append(
            f"handler_kind '{kind}' is not an allowed safe handler "
            f"(allowed: {sorted(_SAFE_HANDLERS)})"
        )

    if not (defn.reason or "").strip():
        errors.append("reason required (why the agent needs this capability)")

    return errors


def security_governance_check(defn: SkillDefinition) -> tuple[bool, list[str]]:
    """
    Fail-closed security review. Returns (allowed_to_proceed, notes).
    Does not grant authority — only gates whether proposal may advance.
    """
    notes: list[str] = []
    risk = defn.risk if isinstance(defn.risk, SkillRisk) else SkillRisk(str(defn.risk))

    # Never allow unrestricted / privilege-escalation shaped proposals
    blob = json.dumps(defn.to_dict(), default=str).lower()
    for token in (
        "bypass",
        "unrestricted",
        "require_authority=false",
        "disable governance",
        "all capabilities",
        "sudo",
        "subprocess",
        "os.system",
        "__import__",
        "eval(",
        "exec(",
        "service_role",
        "secret_key",
        "api_key",
        "raw_shell",
        "drop table",
        "rm -rf",
    ):
        if token in blob:
            notes.append(f"security reject: contains '{token}'")
            return False, notes

    # Capability slug must bind to the skill name (anti-spoofing)
    expected_slug = f"ucip:skill.{defn.name}"
    if (defn.capability_slug or "").strip() != expected_slug:
        notes.append(
            f"capability_slug must equal '{expected_slug}' (got '{defn.capability_slug}')"
        )
        return False, notes

    # Handler must remain in allowlist — agents cannot inject custom code
    if defn.handler_kind not in _SAFE_HANDLERS:
        notes.append("handler_kind not in safe allowlist")
        return False, notes

    # Catalog risk ceiling: safe handlers cannot claim system side effects as low-risk
    catalog = HANDLER_CATALOG.get(defn.handler_kind) or {}
    if defn.side_effect in ("network", "system"):
        notes.append("side_effect elevates privilege surface — approval required")
        # Even if catalog is low, elevated side_effect forces HITL (handled elsewhere)
    if (defn.side_effect or "none") not in ("none", "workspace", "network", "system"):
        notes.append("invalid side_effect")
        return False, notes

    # Reject attempts to request destructive/network via "low" risk with system effect
    if defn.side_effect in ("network", "system") and risk in (SkillRisk.LOW, SkillRisk.MEDIUM):
        notes.append("network/system requires high or critical risk")
        return False, notes

    if risk == SkillRisk.CRITICAL:
        notes.append("critical risk requires human approval before install")
    elif risk == SkillRisk.HIGH:
        notes.append("high risk requires human approval before install")
    else:
        notes.append("risk within auto-installable band after validation")

    return True, notes


def requires_hitl(defn: SkillDefinition) -> bool:
    risk = defn.risk if isinstance(defn.risk, SkillRisk) else SkillRisk(str(defn.risk))
    if risk in (SkillRisk.HIGH, SkillRisk.CRITICAL):
        return True
    if (defn.side_effect or "").lower() in ("network", "system"):
        return True
    return False


def _evidence(action: str, actor_id: str, status: str, metadata: dict) -> str:
    node_id = f"skillacq-{uuid.uuid4().hex[:12]}"
    try:
        from governance.evidence import EvidenceChain, EvidenceChainManager

        chain_id = f"skill-acq-{metadata.get('tenant_id') or metadata.get('user_id') or 'default'}"
        chain = EvidenceChainManager.load(chain_id)
        if chain is None:
            chain = EvidenceChain(
                chain_id=chain_id,
                goal="skill_acquisition",
                identity_context={
                    "user_id": str(metadata.get("user_id") or ""),
                    "actor_id": actor_id or "system",
                    "tenant_id": str(metadata.get("tenant_id") or ""),
                },
            )
        meta = {k: v for k, v in (metadata or {}).items() if k not in ("token", "password", "secret")}
        meta["evidence_id"] = node_id
        chain.add_node(
            action=action,
            actor_id=actor_id or "system",
            status=status,
            metadata=meta,
        )
        chain.save()
    except Exception as e:
        logger.debug("evidence write skipped: %s", type(e).__name__)
    try:
        from governance.audit import AuditLogger, AuditEventType

        AuditLogger().log(
            AuditEventType.SYSTEM if hasattr(AuditEventType, "SYSTEM") else list(AuditEventType)[0],
            actor_id=actor_id or "system",
            tenant_id=str(metadata.get("tenant_id") or "default"),
            action=action,
            outcome=status,
            details={k: v for k, v in metadata.items() if k not in ("token", "secret")},
        )
    except Exception:
        pass
    return node_id


def propose_skill(
    *,
    name: str,
    description: str,
    reason: str,
    requested_by: str,
    tenant_id: str = "default",
    input_schema: Optional[dict] = None,
    risk: str = "medium",
    side_effect: str = "none",
    handler_kind: str = "echo",
    capability_slug: Optional[str] = None,
) -> SkillProposal:
    """Create a draft proposal after missing-capability detection."""
    slug = capability_slug or f"ucip:skill.{name.strip()}"
    try:
        risk_e = SkillRisk(str(risk).lower())
    except Exception:
        risk_e = SkillRisk.MEDIUM

    defn = SkillDefinition(
        name=name.strip(),
        description=description.strip(),
        capability_slug=slug,
        input_schema=input_schema or {"type": "object", "properties": {}},
        risk=risk_e,
        side_effect=side_effect,
        reason=reason.strip(),
        requested_by=requested_by,
        tenant_id=tenant_id,
        handler_kind=handler_kind,
    )
    pid = f"sp_{uuid.uuid4().hex[:16]}"
    prop = SkillProposal(proposal_id=pid, definition=defn, status=ProposalStatus.DRAFT)
    eid = _evidence(
        "skill.propose",
        requested_by,
        "success",
        {"proposal_id": pid, "name": name, "tenant_id": tenant_id, "reason": reason[:500]},
    )
    prop.evidence_ids.append(eid)
    _PROPOSALS[pid] = prop
    return prop


def validate_proposal(proposal_id: str) -> SkillProposal:
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")
    errors = validate_skill_definition(prop.definition)
    ok, notes = security_governance_check(prop.definition)
    prop.validation_errors = errors
    prop.security_notes = notes
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    if errors or not ok:
        prop.status = ProposalStatus.REJECTED
        _evidence(
            "skill.validate",
            prop.definition.requested_by,
            "failed",
            {
                "proposal_id": proposal_id,
                "errors": errors,
                "notes": notes,
                "tenant_id": prop.definition.tenant_id,
            },
        )
        return prop
    prop.status = ProposalStatus.VALIDATED
    _evidence(
        "skill.validate",
        prop.definition.requested_by,
        "success",
        {"proposal_id": proposal_id, "tenant_id": prop.definition.tenant_id},
    )
    return prop


def request_approval(proposal_id: str, user_id: str = "system") -> SkillProposal:
    """Mark privileged proposals as pending HITL approval."""
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")
    if prop.status == ProposalStatus.REJECTED:
        return prop
    if prop.status == ProposalStatus.DRAFT:
        validate_proposal(proposal_id)
        prop = _PROPOSALS[proposal_id]
    if prop.status == ProposalStatus.REJECTED:
        return prop

    if not requires_hitl(prop.definition):
        prop.status = ProposalStatus.APPROVED
        prop.updated_at = datetime.now(timezone.utc).isoformat()
        return prop

    prop.status = ProposalStatus.PENDING_APPROVAL
    hitl_id = f"hitl-skill-{proposal_id}"
    prop.hitl_request_id = hitl_id
    try:
        from governance.hitl import HITLManager, HITLRequest

        mgr = HITLManager()
        if hasattr(mgr, "submit") or hasattr(mgr, "create_request"):
            # Best-effort integration with existing HITL manager shapes.
            req_fn = getattr(mgr, "create_request", None) or getattr(mgr, "submit", None)
            if callable(req_fn):
                try:
                    result = req_fn(
                        action=f"skill.install:{prop.definition.name}",
                        reason=prop.definition.reason,
                        user_id=user_id,
                        risk=prop.definition.risk.value,
                    )
                    if isinstance(result, str):
                        prop.hitl_request_id = result
                    elif hasattr(result, "request_id"):
                        prop.hitl_request_id = result.request_id
                except TypeError:
                    pass
    except Exception as e:
        logger.debug("HITL integration optional: %s", type(e).__name__)

    _evidence(
        "skill.approval_requested",
        prop.definition.requested_by,
        "pending",
        {
            "proposal_id": proposal_id,
            "hitl_request_id": prop.hitl_request_id,
            "tenant_id": prop.definition.tenant_id,
        },
    )
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    return prop


def resolve_approval(proposal_id: str, approved: bool, resolved_by: str = "operator") -> SkillProposal:
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")
    if prop.status not in (ProposalStatus.PENDING_APPROVAL, ProposalStatus.VALIDATED):
        return prop
    # Agents cannot approve their own privileged proposals
    requester = (prop.definition.requested_by or "").strip()
    resolver = (resolved_by or "").strip()
    if (
        approved
        and requester
        and resolver
        and requester == resolver
        and requires_hitl(prop.definition)
    ):
        prop.validation_errors = list(prop.validation_errors or []) + ["self_approval_forbidden"]
        prop.status = ProposalStatus.PENDING_APPROVAL
        _evidence(
            "skill.approval_resolved",
            resolver,
            "denied_self_approval",
            {"proposal_id": proposal_id, "tenant_id": prop.definition.tenant_id},
        )
        return prop
    if approved:
        prop.status = ProposalStatus.APPROVED
        outcome = "approved"
    else:
        prop.status = ProposalStatus.REJECTED
        outcome = "denied"
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    _evidence(
        "skill.approval_resolved",
        resolved_by,
        outcome,
        {"proposal_id": proposal_id, "tenant_id": prop.definition.tenant_id},
    )
    return prop



def _isolation_sample_for(defn: SkillDefinition) -> dict:
    """Handler-aware sample args so isolation tests exercise real code paths."""
    kind = defn.handler_kind
    presets = {
        "echo": {"msg": "test"},
        "json_validate": {"payload": "{}"},
        "hash_text": {"text": "test"},
        "text_transform": {"text": "Hello World", "op": "slugify"},
        "line_stats": {"text": "a\nb\n"},
        "diff_summary": {"before": "a\n", "after": "b\n"},
        "json_path_get": {"payload": {"a": {"b": 1}}, "path": "a.b"},
        "regex_extract": {"text": "abc 123", "pattern": r"\d+"},
        "path_normalize": {"path": "src/app.py"},
        "extension_tally": {"paths": ["a.py", "b.ts", "c.py"]},
        "parse_pytest_summary": {"text": "1 passed in 0.1s"},
        "parse_compiler_errors": {"text": 'File "x.py", line 1'},
        "markdown_outline": {"text": "# Title\n\n## Sub"},
        "csv_preview": {"text": "a,b\n1,2\n", "max_rows": 2},
        "semver_compare": {"a": "1.2.3", "b": "1.2.4"},
    }
    sample = dict(presets.get(kind) or {})
    props = (defn.input_schema or {}).get("properties") or {}
    for k, spec in list(props.items())[:8]:
        if k in sample:
            continue
        typ = (spec or {}).get("type")
        if typ == "string":
            if k in ("payload", "json", "data") or kind == "json_validate":
                sample[k] = "{}"
            else:
                sample[k] = "test"
        elif typ == "integer":
            sample[k] = 0
        elif typ == "boolean":
            sample[k] = False
        elif typ == "object":
            sample[k] = {}
        elif typ == "array":
            sample[k] = []
        else:
            sample[k] = "test"
    return sample


def _isolation_test(defn: SkillDefinition) -> dict:
    """Run the safe handler once — proves callable without network/system side effects.

    Handler logical failures (e.g. invalid JSON input) still count as a successful
    isolation run if the function returns a structured dict without raising.
    """
    fn = _SAFE_HANDLERS.get(defn.handler_kind)
    if not fn:
        return {"ok": False, "error": "no handler"}
    try:
        sample = _isolation_sample_for(defn)
        result = fn(sample)
        if not isinstance(result, dict):
            return {"ok": False, "error": "handler must return dict"}
        return {
            "ok": True,
            "handler_ok": bool(result.get("ok", True)),
            "result_keys": sorted(result.keys()),
            "handler": defn.handler_kind,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def install_skill(proposal_id: str, *, force_approved: bool = False) -> SkillProposal:
    """
    Register capability + agent tool only after validation and approval gates.
    Never installs rejected or unapproved high-risk skills.
    """
    prop = _PROPOSALS.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal {proposal_id}")

    if prop.status == ProposalStatus.DRAFT:
        validate_proposal(proposal_id)
        prop = _PROPOSALS[proposal_id]
    if prop.status == ProposalStatus.REJECTED:
        return prop

    if requires_hitl(prop.definition):
        if prop.status == ProposalStatus.VALIDATED:
            request_approval(proposal_id)
            prop = _PROPOSALS[proposal_id]
        if prop.status == ProposalStatus.PENDING_APPROVAL and not force_approved:
            return prop
        if prop.status not in (ProposalStatus.APPROVED, ProposalStatus.INSTALLED):
            if not force_approved:
                prop.validation_errors.append("privileged skill requires approval before install")
                return prop

    if prop.status not in (
        ProposalStatus.VALIDATED,
        ProposalStatus.APPROVED,
        ProposalStatus.INSTALLED,
    ) and not force_approved:
        prop.validation_errors.append(f"cannot install from status={prop.status.value}")
        return prop

    # Isolation test before registration
    test = _isolation_test(prop.definition)
    prop.isolation_test = test
    if not test.get("ok"):
        prop.status = ProposalStatus.TEST_FAILED
        _evidence(
            "skill.isolation_test",
            prop.definition.requested_by,
            "failed",
            {"proposal_id": proposal_id, "test": test, "tenant_id": prop.definition.tenant_id},
        )
        return prop

    defn = prop.definition
    # Capability registry
    try:
        from governance.capability_registry import (
            CapabilityCategory,
            CapabilityDescriptor,
            CapabilityRisk,
            get_registry,
        )

        risk_map = {
            SkillRisk.LOW: CapabilityRisk.LOW,
            SkillRisk.MEDIUM: CapabilityRisk.MEDIUM,
            SkillRisk.HIGH: CapabilityRisk.HIGH,
            SkillRisk.CRITICAL: CapabilityRisk.CRITICAL,
        }
        cat = CapabilityCategory.SYSTEM
        if defn.side_effect == "none":
            cat = CapabilityCategory.EXECUTION
        elif defn.side_effect == "workspace":
            cat = CapabilityCategory.FILESYSTEM
        elif defn.side_effect == "network":
            cat = CapabilityCategory.NETWORK

        desc = CapabilityDescriptor(
            slug=defn.capability_slug,
            name=defn.name,
            category=cat,
            description=defn.description,
            risk=risk_map.get(defn.risk, CapabilityRisk.MEDIUM),
            input_schema=defn.input_schema or {},
            requires_hitl=requires_hitl(defn),
            requires_network=defn.side_effect == "network",
            is_reversible=defn.side_effect == "none",
        )
        get_registry().register(desc)
        prop.installed_capability = defn.capability_slug
    except Exception as e:
        prop.validation_errors.append(f"capability register failed: {type(e).__name__}")
        prop.status = ProposalStatus.REJECTED
        return prop

    # Agent tool metadata + dynamic handler table. Execution still goes through
    # AgentRuntime._execute_tool → run_dynamic_skill_handler, then UCIP at call site.
    try:
        from brain.agent_tools import (
            AgentTool,
            register_agent_tool,
            AgentMode,
            MODE_TOOLS,
            SideEffect,
            ToolRisk,
        )

        kind = defn.handler_kind
        if kind not in _SAFE_HANDLERS:
            raise ValueError(f"unsafe handler_kind {kind}")

        se = SideEffect.NONE
        if defn.side_effect == "workspace":
            se = SideEffect.LOCAL
        elif defn.side_effect in ("network", "system"):
            se = SideEffect.UNKNOWN

        risk_map = {
            SkillRisk.LOW: ToolRisk.LOW,
            SkillRisk.MEDIUM: ToolRisk.MEDIUM,
            SkillRisk.HIGH: ToolRisk.HIGH,
            SkillRisk.CRITICAL: ToolRisk.CRITICAL,
        }
        tool = AgentTool(
            name=defn.name,
            description=defn.description,
            input_schema=defn.input_schema or {"type": "object", "properties": {}},
            capability=defn.capability_slug,
            side_effect=se,
            risk=risk_map.get(defn.risk, ToolRisk.MEDIUM),
            timeout_s=30,
            durable=False,
        )
        register_agent_tool(tool)
        # Dynamic handler table (name → safe kind)
        _DYNAMIC_HANDLERS[defn.name] = kind
        # Allow in coding modes without granting admin-only tools
        for mode in (AgentMode.AGENT, AgentMode.EDIT):
            if mode in MODE_TOOLS:
                MODE_TOOLS[mode].add(defn.name)
        prop.installed_tool = defn.name
        _INSTALLED[defn.name] = proposal_id
    except Exception as e:
        prop.validation_errors.append(f"tool register failed: {type(e).__name__}: {e}")
        prop.status = ProposalStatus.REJECTED
        return prop

    prop.status = ProposalStatus.INSTALLED
    prop.updated_at = datetime.now(timezone.utc).isoformat()
    eid = _evidence(
        "skill.install",
        prop.definition.requested_by,
        "success",
        {
            "proposal_id": proposal_id,
            "tool": prop.installed_tool,
            "capability": prop.installed_capability,
            "tenant_id": prop.definition.tenant_id,
        },
    )
    prop.evidence_ids.append(eid)
    logger.info(
        "[skill_acquisition] installed tool=%s capability=%s proposal=%s",
        prop.installed_tool,
        prop.installed_capability,
        proposal_id,
    )
    return prop


def get_proposal(proposal_id: str) -> Optional[SkillProposal]:
    return _PROPOSALS.get(proposal_id)


def list_proposals(tenant_id: Optional[str] = None) -> list[dict]:
    rows = list(_PROPOSALS.values())
    if tenant_id:
        rows = [p for p in rows if p.definition.tenant_id == tenant_id]
    return [p.to_dict() for p in rows]


def reset_store_for_tests() -> None:
    """Test helper — clear in-memory proposals (does not wipe CapabilityRegistry)."""
    _PROPOSALS.clear()
    _INSTALLED.clear()
    _DYNAMIC_HANDLERS.clear()


def run_dynamic_skill_handler(name: str, args: dict):
    kind = _DYNAMIC_HANDLERS.get(name)
    if not kind:
        return None
    fn = _SAFE_HANDLERS.get(kind)
    if not fn:
        return {"ok": False, "error": "handler missing"}
    try:
        return fn(args if isinstance(args, dict) else {})
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}



def acquire_skill_pipeline(
    *,
    name: str,
    description: str,
    reason: str,
    requested_by: str,
    tenant_id: str = "default",
    risk: str = "low",
    side_effect: str = "none",
    handler_kind: str = "echo",
    input_schema: Optional[dict] = None,
    auto_approve_low_risk: bool = True,
) -> dict:
    """
    Full governed pipeline for agents:
    detect → propose → validate → (approve if needed) → install → test.
    """
    missing = detect_missing_capability(name)
    if not missing["missing"]:
        return {
            "ok": True,
            "already_present": True,
            "detection": missing,
            "proposal": None,
        }

    prop = propose_skill(
        name=name,
        description=description,
        reason=reason,
        requested_by=requested_by,
        tenant_id=tenant_id,
        risk=risk,
        side_effect=side_effect,
        handler_kind=handler_kind,
        input_schema=input_schema,
    )
    prop = validate_proposal(prop.proposal_id)
    if prop.status == ProposalStatus.REJECTED:
        return {"ok": False, "stage": "validate", "proposal": prop.to_dict()}

    if requires_hitl(prop.definition):
        prop = request_approval(prop.proposal_id, user_id=requested_by)
        return {
            "ok": False,
            "stage": "pending_approval",
            "proposal": prop.to_dict(),
            "message": "Privileged skill requires human approval before installation",
        }

    if auto_approve_low_risk:
        prop = install_skill(prop.proposal_id)
    return {
        "ok": prop.status == ProposalStatus.INSTALLED,
        "stage": prop.status.value,
        "proposal": prop.to_dict(),
        "detection": missing,
    }
