#!/usr/bin/env bash
# DevOS — single install (backend + prebuilt frontend)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
echo "==> DevOS single installer"
command -v python3 >/dev/null || { echo "python3 required (3.12+ recommended)"; exit 1; }
PY_OK=$(python3 -c 'import sys; print("yes" if sys.version_info>=(3,11) else "no")')
[[ "$PY_OK" == "yes" ]] || { echo "Python 3.11+ required"; exit 1; }
REQ=requirements.txt
[[ -f requirements-lite.txt ]] && REQ=requirements-lite.txt
python3 -m pip install --upgrade pip
python3 -m pip install -r "$REQ"
if [[ ! -f .env ]]; then
  [[ -f .env.example ]] && cp .env.example .env || printf 'DEBUG=true\nJWT_SECRET=\nDEFAULT_PROVIDER=ollama\n' > .env
fi
python3 - <<'E'
from pathlib import Path
import re, secrets
p=Path(".env"); t=p.read_text() if p.exists() else ""
if not re.search(r"^JWT_SECRET=.+", t, re.M):
    s=secrets.token_hex(32)
    t=re.sub(r"^JWT_SECRET=.*$", f"JWT_SECRET={s}", t, flags=re.M) if re.search(r"^JWT_SECRET=",t,re.M) else t+f"\nJWT_SECRET={s}\n"
    p.write_text(t); print("==> JWT_SECRET set")
E
if [[ ! -f frontend/index.html && ! -f frontend/templates/index.html ]]; then
  echo "ERROR: prebuilt frontend missing"; exit 1
fi
mkdir -p frontend/templates
[[ -f frontend/index.html && ! -f frontend/templates/index.html ]] && cp frontend/index.html frontend/templates/index.html
[[ -d frontend/static ]] || { echo "ERROR: frontend/static missing"; exit 1; }
echo "✅ Done. Start: python3 cli.py start → http://localhost:8000"
