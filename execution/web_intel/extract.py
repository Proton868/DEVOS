"""Public HTML extraction — no fabricated fields."""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin


def extract_html(html: str, *, base_url: str = "") -> dict[str, Any]:
    html = html or ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None
    desc_m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']',
        html, re.I,
    ) or re.search(
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']description["\']',
        html, re.I,
    )
    description = desc_m.group(1).strip() if desc_m else None
    canon_m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
    canonical = urljoin(base_url, canon_m.group(1)) if canon_m else None
    lang_m = re.search(r'<html[^>]+lang=["\']([^"\']+)["\']', html, re.I)
    language = lang_m.group(1) if lang_m else None

    # OpenGraph
    og = {}
    for m in re.finditer(r'<meta[^>]+property=["\']og:([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']', html, re.I):
        og[m.group(1)] = m.group(2)

    # JSON-LD
    json_ld = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                json_ld.extend(data)
            else:
                json_ld.append(data)
        except json.JSONDecodeError:
            continue

    links = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1).strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full = urljoin(base_url, href)
        links.append(full)

    social_hosts = (
        "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
        "youtube.com", "tiktok.com", "github.com",
    )
    social = []
    for l in links:
        low = l.lower()
        if any(h in low for h in social_hosts):
            social.append(l)

    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:20000]

    headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, re.I | re.S)
    headings = [re.sub(r"<[^>]+>", "", h).strip() for h in headings][:30]

    return {
        "title": title,
        "description": description,
        "canonical_url": canonical,
        "language": language,
        "open_graph": og or None,
        "json_ld": json_ld or None,
        "links": links[:500],
        "social_links": list(dict.fromkeys(social))[:30],
        "headings": headings,
        "extracted_text": text or None,
    }
