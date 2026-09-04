"""Map natural language to delivery intents/capabilities — does NOT execute."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from brain.capability_canon import canonicalize, to_ucip


@dataclass(frozen=True)
class DeliveryIntent:
    intent: str
    capability: str
    goal_hint: str
    external_side_effect: bool = False

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "capability": self.capability,
            "ucip_capability": to_ucip(self.capability),
            "goal_hint": self.goal_hint,
            "external_side_effect": self.external_side_effect,
            "note": "Intent only — Mission Engine + UCIP authorize execution",
        }


_PATTERNS: list[tuple[re.Pattern, DeliveryIntent]] = [
    (re.compile(r"\binspect\b|\bcheck\b.*\bproject\b|\blook at\b", re.I),
     DeliveryIntent("inspect", "filesystem.read", "inspect", False)),
    (re.compile(r"\bverify\b|\bverify that\b|\bworks\b", re.I),
     DeliveryIntent("verify", "filesystem.read", "verify", False)),
    (re.compile(r"\btest(s)?\b", re.I),
     DeliveryIntent("verify", "filesystem.read", "verify", False)),
    (re.compile(r"\bbuild\b", re.I),
     DeliveryIntent("build", "shell.execute", "build", False)),
    (re.compile(r"\binstall\b|\bnpm i\b|\bpip install\b", re.I),
     DeliveryIntent("install", "package.install", "install", False)),
    (re.compile(r"\bpreview\b|\bshow (me )?the (running )?app\b|\bshow (me )?the result\b|\brunning app\b", re.I),
     DeliveryIntent("preview", "preview.serve", "preview", False)),
    (re.compile(r"\bcommit\b", re.I),
     DeliveryIntent("github_commit", "vcs.write", "commit", False)),
    (re.compile(r"\bpush\b.*\b(git|github)?|\bpush (it )?to github\b", re.I),
     DeliveryIntent("github_push", "vcs.push", "github push", True)),
    (re.compile(r"\bpull request\b|\b\bpr\b|\bopen a pr\b", re.I),
     DeliveryIntent("github_pr", "vcs.push", "github pr", True)),
    (re.compile(r"\bdeploy\b.*\bvercel\b|\bvercel\b", re.I),
     DeliveryIntent("deploy_vercel", "deployment.production", "deploy to vercel", True)),
    (re.compile(r"\bdeploy\b.*\bnetlify\b|\bnetlify\b", re.I),
     DeliveryIntent("deploy_netlify", "deployment.production", "deploy to netlify", True)),
    (re.compile(r"\bcloudflare\b|\btunnel\b", re.I),
     DeliveryIntent("deploy_cloudflare", "deployment.production", "cloudflare tunnel", True)),
    (re.compile(r"\bdeploy\b", re.I),
     DeliveryIntent("deploy", "deployment.production", "deploy", True)),
    (re.compile(r"\bpublish\b|\bmake (it )?public\b", re.I),
     DeliveryIntent("publish", "external.publish", "publish", True)),
    (re.compile(r"\bshare\b", re.I),
     DeliveryIntent("share", "external.publish", "share", False)),
    (re.compile(
        r"\b(crawl|research|analyze)\b.*\b(website|site|business|company|online presence)\b|"
        r"\bfind everything publicly available\b|"
        r"\bpublic (website|business|social)\b|"
        r"\bresearch this (website|business|company)\b|"
        r"\bcrawl this (website|site)\b",
        re.I,
    ), DeliveryIntent("web_crawl", "web.intelligence", "web research", False)),
]

def crawl_budgets_from_text(message: str) -> dict:
    """Safe bounded budgets from NL — never unbounded."""
    m = (message or "").lower()
    if any(x in m for x in ("quickly", "quick check", "glance", "shallow")):
        return {"max_depth": 1, "max_pages": 8, "max_requests": 12, "max_bytes": 1_000_000}
    if any(x in m for x in ("entire", "whole website", "every public", "all pages", "deep")):
        return {"max_depth": 3, "max_pages": 80, "max_requests": 120, "max_bytes": 8_000_000}
    return {"max_depth": 2, "max_pages": 30, "max_requests": 50, "max_bytes": 3_000_000}


def classify_delivery_intent(message: str) -> Optional[DeliveryIntent]:
    text = (message or "").strip()
    if not text:
        return None
    # prefer more specific patterns first (already ordered)
    for pat, intent in _PATTERNS:
        if pat.search(text):
            return intent
    return None


def nuha_can_request(capability: str, nuha_capabilities: list[str]) -> bool:
    """Whether Nuha may REQUEST this capability (not whether UCIP will allow)."""
    can = canonicalize(capability)
    allowed = {canonicalize(c) for c in (nuha_capabilities or [])}
    return can in allowed or capability in allowed
