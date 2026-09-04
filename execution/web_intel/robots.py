"""Minimal robots.txt parser — respect by default."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urljoin
import urllib.request


@dataclass
class RobotsRules:
    sitemaps: list[str] = field(default_factory=list)
    allows: list[str] = field(default_factory=list)
    disallows: list[str] = field(default_factory=list)
    crawl_delay: Optional[float] = None
    raw: str = ""
    source_url: str = ""
    error: Optional[str] = None

    def allowed(self, path: str) -> tuple[bool, str]:
        path = path or "/"
        # longest match wins among allow/disallow
        best_allow = ""
        best_disallow = ""
        for a in self.allows:
            if path.startswith(a) and len(a) >= len(best_allow):
                best_allow = a
        for d in self.disallows:
            if d == "" or d == "/":
                # empty disallow means allow all in some parsers; treat "/" as block all
                if d == "/":
                    best_disallow = d
                continue
            if path.startswith(d) and len(d) >= len(best_disallow):
                best_disallow = d
        if best_allow and len(best_allow) >= len(best_disallow):
            return True, f"allow:{best_allow}"
        if best_disallow:
            return False, f"disallow:{best_disallow}"
        return True, "default_allow"


def parse_robots(text: str, *, source_url: str = "") -> RobotsRules:
    rules = RobotsRules(raw=text or "", source_url=source_url)
    if not text:
        return rules
    ua_block = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key == "user-agent":
            ua_block = val == "*" or val.lower().startswith("devos")
        elif not ua_block:
            continue
        elif key == "disallow":
            rules.disallows.append(val)
        elif key == "allow":
            rules.allows.append(val)
        elif key == "crawl-delay":
            try:
                rules.crawl_delay = float(val)
            except ValueError:
                pass
        elif key == "sitemap":
            rules.sitemaps.append(val)
    return rules


def fetch_robots(root_url: str, *, timeout: float = 10.0) -> RobotsRules:
    from execution.web_intel.safety import is_url_allowed, normalize_url
    p = urlparse(normalize_url(root_url) or root_url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    ok, reason = is_url_allowed(robots_url)
    if not ok:
        return RobotsRules(error=f"blocked:{reason}", source_url=robots_url)
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": "DevOS-WebIntel/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000).decode("utf-8", errors="replace")
        return parse_robots(raw, source_url=robots_url)
    except Exception as e:
        # missing robots → allow by convention but record
        return RobotsRules(error=f"{type(e).__name__}", source_url=robots_url)
