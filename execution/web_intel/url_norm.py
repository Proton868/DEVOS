"""URL normalization for crawl frontier deduplication."""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_eid$|_ga$|ref$)")


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    if not host:
        return ""
    port = p.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    # filter tracking query params only
    pairs = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if _TRACKING.match(k) or k.lower().startswith("utm_"):
            continue
        pairs.append((k, v))
    query = urlencode(pairs, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))  # drop fragment


def same_domain(a: str, b: str, *, include_subdomains: bool = False) -> bool:
    ha = (urlparse(a).hostname or "").lower()
    hb = (urlparse(b).hostname or "").lower()
    if not ha or not hb:
        return False
    if ha == hb:
        return True
    if include_subdomains:
        return ha.endswith("." + hb) or hb.endswith("." + ha)
    return False
