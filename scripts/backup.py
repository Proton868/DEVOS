#!/usr/bin/env python3
"""
DevOS Backup Script

Dumps the active database (SQLite or Postgres), archives data/evidence/
and the secrets vault's encrypted storage file, and writes a single
timestamped tarball to a configurable output directory.

Usage:
    python3 scripts/backup.py                    # Default: ./backups/
    python3 scripts/backup.py -o /mnt/backups    # Custom output dir
    python3 scripts/backup.py --keep-last 7       # Keep last 7 backups only

Note: JWT_SECRET/.env is NOT backed up — secrets management is separate.
"""
import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config import settings


def backup_database(work_dir: Path) -> Path:
    """Dump the database to work_dir/db_dump.sql (SQLite) or work_dir/db_dump.pg (Postgres)."""
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite"):
        sqlite_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        if not os.path.isabs(sqlite_path):
            sqlite_path = str(BASE_DIR / sqlite_path.lstrip("./"))
        if not os.path.exists(sqlite_path):
            print(f"  WARNING: sqlite database not found at {sqlite_path}, skipping")
            return None
        dump_path = work_dir / "db_dump.sql"
        subprocess.run(
            ["sqlite3", sqlite_path, ".dump"],
            stdout=open(dump_path, "w"),
            stderr=subprocess.PIPE,
            check=True,
        )
        print(f"  Dumped SQLite database to {dump_path}")
        return dump_path
    elif db_url.startswith("postgresql"):
        dump_path = work_dir / "db_dump.pg"
        subprocess.run(
            ["pg_dump", settings.DATABASE_URL, "-f", str(dump_path)],
            check=True,
        )
        print(f"  Dumped Postgres database to {dump_path}")
        return dump_path
    else:
        print(f"  WARNING: unsupported database URL scheme, skipping database backup")
        return None


def backup_evidence(work_dir: Path) -> Path:
    """Archive data/evidence/ directory."""
    src = BASE_DIR / "data" / "evidence"
    if not src.exists() or not any(src.iterdir()):
        print("  No evidence files to back up")
        return None
    dst = work_dir / "evidence.tar.gz"
    with tarfile.open(dst, "w:gz") as tar:
        tar.add(src, arcname="evidence")
    print(f"  Archived {sum(1 for _ in src.iterdir())} evidence files to {dst}")
    return dst


def backup_secrets(work_dir: Path) -> Path:
    """Back up the encrypted secrets database rows. Since they're already
    encrypted at rest, we just copy the SQLite database file."""
    secrets_db = BASE_DIR / "data" / "devos.db"
    if not secrets_db.exists():
        # Try the configured database path
        db_url = settings.DATABASE_URL
        if db_url.startswith("sqlite"):
            path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
            if not os.path.isabs(path):
                path = str(BASE_DIR / path.lstrip("./"))
            secrets_db = Path(path)
    if not secrets_db.exists():
        print("  No secrets database found to back up")
        return None
    dst = work_dir / "secrets.db"
    shutil.copy2(secrets_db, dst)
    print(f"  Copied secrets database to {dst}")
    return dst


def main():
    parser = argparse.ArgumentParser(description="DevOS Backup Script")
    parser.add_argument("-o", "--output-dir", default=str(BASE_DIR / "backups"),
                        help="Output directory for backup tarballs (default: ./backups/)")
    parser.add_argument("--keep-last", type=int, default=0,
                        help="Keep only the last N backups, deleting older ones")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work_dir = Path(tempfile.mkdtemp(prefix="devos-backup-"))

    try:
        print("DevOS Backup")
        print(f"  Timestamp: {timestamp}")

        db_dump = backup_database(work_dir)
        evidence_archive = backup_evidence(work_dir)
        secrets_copy = backup_secrets(work_dir)

        if not db_dump and not evidence_archive and not secrets_copy:
            print("Nothing to back up — aborting.")
            sys.exit(1)

        # Create tarball
        tarball = output_dir / f"devos-backup-{timestamp}.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(work_dir, arcname="")
        print(f"  Backup complete: {tarball} ({tarball.stat().st_size:,} bytes)")

        # Retention: delete old backups beyond keep-last
        if args.keep_last > 0:
            backups = sorted(output_dir.glob("devos-backup-*.tar.gz"), reverse=True)
            for old in backups[args.keep_last:]:
                old.unlink()
                print(f"  Removed old backup: {old.name}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()