"""Sitemap discovery with size limits (no XXE — use ElementTree without external entities)."""
from __future__ import annotations

import re
import urllib.request
from typing import Optional
from xml.etree import ElementTree as ET

from execution.web_intel.safety import is_url_allowed, normalize_url

_MAX_SITEMAP_BYTES = 2_000_000
_MAX_URLS = 500


def _parse_sitemap_xml(text: str) -> list[str]:
    urls = []
    try:
        # strip DOCTYPE to reduce XXE risk with stdlib parser
        text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
        root = ET.fromstring(text)
    except ET.ParseError:
        return urls
    tag = root.tag.lower()
    # namespace-agnostic
    for el in root.iter():
        local = el.tag.split("}")[-1].lower()
        if local == "loc" and el.text:
            urls.append(el.text.strip())
            if len(urls) >= _MAX_URLS:
                break
    return urls


def fetch_sitemap_urls(url: str, *, timeout: float = 15.0, depth: int = 0) -> list[str]:
    if depth > 2:
        return []
    nu = normalize_url(url)
    ok, reason = is_url_allowed(nu)
    if not ok:
        return []
    try:
        req = urllib.request.Request(nu, headers={"User-Agent": "DevOS-WebIntel/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_SITEMAP_BYTES + 1)
            if len(raw) > _MAX_SITEMAP_BYTES:
                raw = raw[:_MAX_SITEMAP_BYTES]
            text = raw.decode("utf-8", errors="replace")
    except Exception:
        return []
    locs = _parse_sitemap_xml(text)
    out = []
    for loc in locs:
        if loc.endswith(".xml") and "sitemap" in loc.lower() and depth < 2:
            out.extend(fetch_sitemap_urls(loc, timeout=timeout, depth=depth + 1))
        else:
            out.append(loc)
        if len(out) >= _MAX_URLS:
            break
    return out[:_MAX_URLS]
