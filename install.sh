#!/usr/bin/env bash
# DevOS — one-step local install (self-contained: SQLite + prebuilt UI)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> DevOS installer"
command -v python3 >/dev/null || { echo "python3 required (3.11+)"; exit 1; }
PY_OK=$(python3 -c 'import sys; print("yes" if sys.version_info >= (3, 11) else "no")')
[[ "$PY_OK" == "yes" ]] || { echo "Python 3.11+ required (3.12/3.13 recommended)"; exit 1; }

# Prefer lite deps for fastest install; fall back to full pin set
REQ=requirements.txt
if [[ -f requirements-lite.txt ]]; then
  REQ=requirements-lite.txt
fi
echo "==> Installing Python packages ($REQ)"
python3 -m pip install --upgrade pip -q
python3 -m pip install -r "$REQ"

# .env from example; generate JWT_SECRET if empty
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
  else
    printf 'DEBUG=true\nJWT_SECRET=\nDEFAULT_PROVIDER=ollama\nDATABASE_URL=sqlite+aiosqlite:///./data/devos.db\n' > .env
  fi
  echo "==> Created .env"
fi
python3 - <<'PY'
from pathlib import Path
import re, secrets
p = Path(".env")
t = p.read_text() if p.exists() else ""
if not re.search(r"^JWT_SECRET=.+", t, re.M) or re.search(r"^JWT_SECRET=\s*$", t, re.M):
    s = secrets.token_hex(32)
    if re.search(r"^JWT_SECRET=", t, re.M):
        t = re.sub(r"^JWT_SECRET=.*$", f"JWT_SECRET={s}", t, flags=re.M)
    else:
        t += f"\nJWT_SECRET={s}\n"
    p.write_text(t)
    print("==> Generated JWT_SECRET")
PY

# Prebuilt frontend is required (no Node needed to run)
if [[ ! -f frontend/index.html ]]; then
  echo "ERROR: frontend/index.html missing — this repo should ship a prebuilt UI"
  exit 1
fi
mkdir -p frontend/templates data workspace
[[ -f frontend/templates/index.html ]] || cp frontend/index.html frontend/templates/index.html
[[ -d frontend/static ]] || { echo "ERROR: frontend/static missing"; exit 1; }

# Ensure data dir for SQLite
mkdir -p data

echo ""
echo "✅ Install complete (self-contained: SQLite + local files)"
echo ""
echo "   Start:   python3 cli.py start"
echo "   Open:    http://localhost:8000"
echo "   Health:  python3 cli.py doctor"
echo "   Optional LLM: install Ollama, or set OPENROUTER_API_KEY in .env"
echo ""
