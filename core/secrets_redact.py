"""Redact credentials from logs and exception text."""
from __future__ import annotations

import logging
import re

_URL_CREDS = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)([^/@\s:]+):([^@/\s]+)@",
)

# KEY=value including OPENROUTER_API_KEY / JWT_SECRET style names
_KEY_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|api[_-]?key|token|authorization|jwt[_-]?secret|encryption[_-]?key|database_url|bearer)[A-Z0-9_]*)(\s*[=:]\s*)([^\s,;]+)",
)

_BEARER = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-+/=]+)")

# Raw secret material (sk-, ghp_, JWT segments)
_SECRET_MATERIAL = re.compile(
    r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"
)

_SENSITIVE_ENV = {
    "PASSWORD", "SECRET", "TOKEN", "API_KEY", "JWT", "ENCRYPTION", "DATABASE_URL", "SUPABASE_KEY",
}


def redact_text(value: str) -> str:
    if not value or not isinstance(value, str):
        return value
    s = value
    s = _URL_CREDS.sub(r"\1***:***@", s)
    s = _KEY_ASSIGN.sub(r"\1\2***", s)
    s = _BEARER.sub(r"\1 ***", s)
    s = _SECRET_MATERIAL.sub("***", s)
    return s


def redact_mapping(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        key = str(k)
        if any(x in key.upper() for x in _SENSITIVE_ENV):
            out[key] = "***" if v else v
        elif isinstance(v, str):
            out[key] = redact_text(v)
        else:
            out[key] = v
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact_mapping(record.args)
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact_text(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            pass
        return True


def install_redacting_filter(root=None) -> None:
    flt = RedactingFilter()
    target = root or logging.getLogger()
    target.addFilter(flt)
    for h in list(target.handlers):
        h.addFilter(flt)
