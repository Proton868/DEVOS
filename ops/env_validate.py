#!/usr/bin/env python3
"""Validate DevOS environment without printing secret values."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _is_sqlite(url: str) -> bool:
    return (url or "").lower().startswith("sqlite")


def _is_postgres(url: str) -> bool:
    return (url or "").lower().startswith("postgres")


def validate(*, production: bool) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    url = (os.environ.get("DATABASE_URL") or "").strip()
    require_pg = _truthy("REQUIRE_POSTGRES", "true")

    if not url:
        errors.append("DATABASE_URL is required")
    elif require_pg and _is_sqlite(url):
        errors.append("REQUIRE_POSTGRES=true forbids SQLite DATABASE_URL")
    elif require_pg and not _is_postgres(url):
        errors.append("DATABASE_URL must be Postgres when REQUIRE_POSTGRES=true")
    elif _is_sqlite(url):
        if production:
            errors.append("Production must not use SQLite DATABASE_URL")
        else:
            warnings.append("SQLite DATABASE_URL is non-production only")

    jwt = (os.environ.get("JWT_SECRET") or "").strip()
    if not jwt or jwt in ("change-me", "secret", "dev", "test"):
        if production:
            errors.append("JWT_SECRET must be a strong non-default value in production")
        else:
            warnings.append("JWT_SECRET is empty or weak")
    elif production and len(jwt) < 32:
        errors.append("JWT_SECRET should be at least 32 characters in production")

    if production and _truthy("DEBUG", "false"):
        errors.append("DEBUG must not be true in production")

    auth = (os.environ.get("AUTH_MODE") or "local").strip().lower()
    if production and auth not in ("local", "supabase", "dual"):
        errors.append("AUTH_MODE invalid: %s" % auth)

    provider = (os.environ.get("DEFAULT_PROVIDER") or "omniroute").strip().lower()
    if provider != "omniroute":
        warnings.append("DEFAULT_PROVIDER=%s (canonical default is omniroute)" % provider)

    admin_pw = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    if production and admin_pw in ("", "admin", "password", "123456", "123456.."):
        errors.append("ADMIN_PASSWORD must not be weak/default in production")

    enc = (os.environ.get("ENCRYPTION_KEY") or "").strip()
    if production and not enc:
        warnings.append("ENCRYPTION_KEY not set")

    return errors, warnings


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", default=".env")
    p.add_argument("--production", action="store_true")
    p.add_argument("--require-file", action="store_true")
    args = p.parse_args()
    path = Path(args.env_file)
    if args.require_file and not path.is_file():
        print("ERROR: env file not found: %s" % path, file=sys.stderr)
        return 2
    _load_dotenv(path)
    errors, warnings = validate(production=args.production)
    for w in warnings:
        print("WARN: %s" % w)
    for e in errors:
        print("ERROR: %s" % e)
    if errors:
        print("env_validate: FAILED")
        return 1
    print("env_validate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
