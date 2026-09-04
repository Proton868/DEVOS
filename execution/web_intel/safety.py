"""SSRF and public-only crawl guards."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

_PRIVATE_HOSTS = {"localhost", "metadata.google.internal", "metadata"}


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    host = (p.hostname or "").lower()
    path = p.path or "/"
    # drop fragments, normalize
    return urlunparse((p.scheme.lower(), host + (f":{p.port}" if p.port else ""), path, "", p.query, ""))


def _host_resolves_private(host: str) -> bool:
    if not host or host in _PRIVATE_HOSTS or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # fail closed
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
            if (
                addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast
            ):
                return True
            # AWS/GCP metadata
            if str(addr) in ("169.254.169.254", "169.254.170.2"):
                return True
        except ValueError:
            return True
    return False


def is_url_allowed(url: str, *, allowlist: list[str] | None = None) -> tuple[bool, str]:
    import os
    if os.environ.get('DEVOS_WEB_INTEL_TEST_ALLOW_LOCALHOST') == '1':
        host = (urlparse(normalize_url(url) or url).hostname or '').lower()
        if host in ('127.0.0.1', 'localhost'):
            return True, 'test_localhost_override'
    nu = normalize_url(url)
    if not nu:
        return False, "empty_url"
    p = urlparse(nu)
    if p.scheme not in ("http", "https"):
        return False, "scheme_not_allowed"
    host = (p.hostname or "").lower()
    if not host:
        return False, "no_host"
    if _host_resolves_private(host):
        return False, "private_or_internal_host"
    if allowlist:
        allowed = False
        for d in allowlist:
            d = d.lower().lstrip(".")
            if host == d or host.endswith("." + d):
                allowed = True
                break
        if not allowed:
            return False, "not_in_allowlist"
    return True, "ok"
