"""Provider-neutral Web Intelligence — public pages only, budgeted."""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from execution.web_intel.safety import is_url_allowed, normalize_url


@dataclass
class WebIntelligenceResult:
    source_url: str
    canonical_url: str = ""
    title: str = ""
    content: str = ""
    extracted_entities: dict = field(default_factory=dict)
    social_links: list = field(default_factory=list)
    contact_points: list = field(default_factory=list)
    crawl_metadata: dict = field(default_factory=dict)
    confidence: Optional[float] = None
    error: Optional[str] = None
    trace_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class WebIntelligenceProvider:
    name = "base"

    def fetch_public(self, url: str, *, max_bytes: int = 500_000, timeout: float = 15.0) -> WebIntelligenceResult:
        raise NotImplementedError


class HttpWebIntelligenceProvider(WebIntelligenceProvider):
    """Minimal public HTTP fetch with hard safety bounds. Not a full crawler platform."""
    name = "http_public"

    def fetch_public(
        self,
        url: str,
        *,
        max_bytes: int = 500_000,
        timeout: float = 15.0,
        allowlist: Optional[list[str]] = None,
        cancel_check=None,
    ) -> WebIntelligenceResult:
        nu = normalize_url(url)
        ok, reason = is_url_allowed(nu, allowlist=allowlist)
        if not ok:
            return WebIntelligenceResult(source_url=url, error=f"blocked:{reason}")
        if cancel_check and cancel_check():
            return WebIntelligenceResult(source_url=nu, error="cancelled")
        try:
            import urllib.request
            req = urllib.request.Request(
                nu,
                headers={"User-Agent": "DevOS-WebIntel/1.0 (+public-research; respect robots)"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]
                ctype = resp.headers.get("Content-Type", "")
                final = resp.geturl()
            text = raw.decode("utf-8", errors="replace")
            title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
            # crude public link extraction
            links = re.findall(r'href=["\'](https?://[^"\']+)["\']', text, re.I)
            social = [l for l in links if any(s in l.lower() for s in (
                "twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com", "github.com",
            ))][:20]
            # strip tags for content excerpt
            content = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
            content = re.sub(r"<style[^>]*>.*?</style>", " ", content, flags=re.I | re.S)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()[:8000]
            emails = list(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", content)))[:10]
            return WebIntelligenceResult(
                source_url=url,
                canonical_url=final or nu,
                title=title,
                content=content,
                social_links=list(dict.fromkeys(social)),
                contact_points=[{"type": "email", "value": e} for e in emails],
                crawl_metadata={
                    "content_type": ctype,
                    "bytes": len(raw),
                    "provider": self.name,
                    "content_hash": hashlib.sha256(raw).hexdigest()[:16],
                    "fetched_at": time.time(),
                },
                confidence=0.5 if content else 0.1,
            )
        except Exception as e:
            return WebIntelligenceResult(source_url=nu, error=f"{type(e).__name__}:{str(e)[:200]}")


def get_web_intel_provider() -> WebIntelligenceProvider:
    kind = (os.environ.get("DEVOS_WEB_INTEL_PROVIDER") or "http_public").strip().lower()
    if kind in ("off", "null", "none"):
        class Off(WebIntelligenceProvider):
            name = "off"
            def fetch_public(self, url: str, **kwargs):
                return WebIntelligenceResult(source_url=url, error="WEB_INTEL_DISABLED")
        return Off()
    return HttpWebIntelligenceProvider()
