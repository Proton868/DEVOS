"""Production schema is migration-owned — never SQLAlchemy create_all on Postgres."""
from __future__ import annotations

import pathlib



def test_sync_session_create_all_guarded_by_sqlite_dialect():
    """create_all in sync_session must only run when dialect is sqlite."""
    src = pathlib.Path("core/sync_session.py").read_text(encoding="utf-8")
    assert 'startswith("sqlite")' in src or "startswith('sqlite')" in src
    lines = src.splitlines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if "Base.metadata.create_all" in stripped:
            found = True
            window = "\n".join(lines[max(0, i - 15) : i + 1])
            assert "sqlite" in window.lower(), (
                f"create_all at line {i+1} not gated by sqlite dialect:\n{window}"
            )
    assert found, "expected Base.metadata.create_all call for sqlite tests"


def test_init_db_never_create_all_on_postgres():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    start = src.find("async def init_db")
    block = src[start : start + 1500]
    assert "postgresql" in block
    assert "Never create_all" in block or "never create" in block.lower()
    assert "DEVOS_SCHEMA_CREATE_ALL" not in block
    # create_all only after postgres early-return
    assert "return" in block
    assert "create_all" in block


def test_no_unconditional_create_all_in_production_modules():
    roots = ["core", "execution", "governance", "memory", "brain", "api"]
    offenders = []
    for root in roots:
        path = pathlib.Path(root)
        for f in path.rglob("*.py"):
            if f.name in ("sync_session.py", "database.py"):
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if "create_all" in stripped and not stripped.startswith("#"):
                    offenders.append(f"{f}:{i}:{stripped}")
    assert offenders == [], offenders


def test_require_postgres_rejects_sqlite_url():
    from ops.env_validate import validate
    import os

    old = dict(os.environ)
    try:
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/x.db"
        os.environ["REQUIRE_POSTGRES"] = "true"
        os.environ["JWT_SECRET"] = "x" * 40
        os.environ["DEBUG"] = "false"
        os.environ["ADMIN_PASSWORD"] = "strong-password-not-default-99"
        os.environ["DEFAULT_PROVIDER"] = "omniroute"
        errors, _ = validate(production=True)
        assert any("SQLite" in e or "Postgres" in e for e in errors)
    finally:
        os.environ.clear()
        os.environ.update(old)
