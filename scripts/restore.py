#!/usr/bin/env python3
"""
DevOS Restore Script

Restores a backup tarball created by scripts/backup.py: restores the database
dump, evidence files, and encrypted secrets database.

Usage:
    python3 scripts/restore.py devos-backup-20260101-120000.tar.gz
    python3 scripts/restore.py devos-backup-20260101-120000.tar.gz --yes  # non-interactive

WARNING: This overwrites existing data. Use with caution.
"""
import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config import settings


def restore_database(work_dir: Path) -> bool:
    """Restore the database from the dump."""
    db_url = settings.DATABASE_URL

    if db_url.startswith("sqlite"):
        sqlite_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        if not os.path.isabs(sqlite_path):
            sqlite_path = str(BASE_DIR / sqlite_path.lstrip("./"))
        sqlite_path = Path(sqlite_path)

        dump_file = work_dir / "db_dump.sql"
        if not dump_file.exists():
            print("  No database dump found in backup — skipping")
            return False

        # Backup existing database first
        if sqlite_path.exists():
            backup = sqlite_path.with_suffix(".db.bak")
            shutil.copy2(sqlite_path, backup)
            print(f"  Existing database backed up to {backup}")

        sqlite_path.unlink(missing_ok=True)
        subprocess.run(
            ["sqlite3", str(sqlite_path), f".read {dump_file}"],
            check=True,
        )
        print(f"  Restored SQLite database to {sqlite_path}")
        return True

    elif db_url.startswith("postgresql"):
        dump_file = work_dir / "db_dump.pg"
        if not dump_file.exists():
            print("  No database dump found in backup — skipping")
            return False
        subprocess.run(
            ["psql", settings.DATABASE_URL, "-f", str(dump_file)],
            check=True,
        )
        print("  Restored Postgres database")
        return True
    else:
        print("  Unknown database type — skipping database restore")
        return False


def restore_evidence(work_dir: Path) -> bool:
    """Restore evidence files from archive."""
    evidence_archive = work_dir / "evidence.tar.gz"
    if not evidence_archive.exists():
        print("  No evidence archive in backup — skipping")
        return False

    evidence_dir = BASE_DIR / "data" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(evidence_archive, "r:gz") as tar:
        # Extract evidence/ subdirectory, stripping the "evidence/" prefix
        for member in tar.getmembers():
            if member.name.startswith("evidence/") and not member.isdir():
                # Strip prefix
                rel = member.name[len("evidence/"):]
                target = evidence_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as f:
                    target.write_bytes(f.read())
    print(f"  Restored evidence files to {evidence_dir}")
    return True


def restore_secrets(work_dir: Path) -> bool:
    """Restore the encrypted secrets database."""
    secrets_file = work_dir / "secrets.db"
    if not secrets_file.exists():
        print("  No secrets database in backup — skipping")
        return False

    db_url = settings.DATABASE_URL
    if not db_url.startswith("sqlite"):
        print("  Secrets restore only supported for SQLite — skipping")
        return False

    target = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    if not os.path.isabs(target):
        target = str(BASE_DIR / target.lstrip("./"))
    target = Path(target)

    if target.exists():
        backup = target.with_suffix(".db.bak")
        shutil.copy2(target, backup)
        print(f"  Existing secrets database backed up to {backup}")

    shutil.copy2(secrets_file, target)
    print(f"  Restored secrets database to {target}")
    return True


def main():
    parser = argparse.ArgumentParser(description="DevOS Restore Script")
    parser.add_argument("backup", help="Path to backup tarball")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt (for non-interactive use)")
    args = parser.parse_args()

    backup_path = Path(args.backup)
    if not backup_path.exists():
        print(f"Backup file not found: {backup_path}")
        sys.exit(1)

    print("DevOS Restore")
    print(f"  Backup: {backup_path}")
    print(f"  Database URL: {settings.DATABASE_URL}")
    print()
    print("WARNING: This will overwrite the current database, evidence files,")
    print("and encrypted secrets with the contents of this backup.")
    print()

    if not args.yes:
        response = input("Continue? [y/N] ").strip().lower()
        if response != "y":
            print("Aborted.")
            sys.exit(0)

    work_dir = Path(tempfile.mkdtemp(prefix="devos-restore-"))
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(work_dir)
        print("  Extracted backup")

        restore_database(work_dir)
        restore_evidence(work_dir)
        restore_secrets(work_dir)

        print(f"\nRestore complete from {backup_path.name}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()