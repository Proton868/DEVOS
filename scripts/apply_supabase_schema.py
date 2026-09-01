#!/usr/bin/env python3
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT/"data"/"supabase_migration.sql", ROOT/"data"/"supabase_rls_complete.sql"]
def main():
    dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL") or ""
    if not dsn or dsn.startswith("sqlite"):
        print("Set SUPABASE_DB_URL to apply schema. Files:")
        for f in FILES: print(" ", f)
        sys.exit(1)
    import psycopg2
    conn = psycopg2.connect(dsn); conn.autocommit = True; cur = conn.cursor()
    for path in FILES:
        if not path.exists(): continue
        print("Applying", path.name)
        try: cur.execute(path.read_text()); print(" OK")
        except Exception as e: print(" WARN", e)
    cur.close(); conn.close()
if __name__ == "__main__": main()
