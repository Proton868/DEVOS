#!/usr/bin/env bash
# DevOS — one-step local install (self-contained: SQLite + prebuilt UI)
# Uses a project-local .venv so no global pip / --break-system-packages is needed.
# Safe on Ubuntu 24.04+ with PEP 668 (externally-managed-environment).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> DevOS installer"
echo "    Location: $ROOT"

# ---------------------------------------------------------------------------
# Python version check
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required (3.11+)."
  exit 1
fi

PY_OK="$(python3 -c 'import sys; print("yes" if sys.version_info >= (3, 11) else "no")')"
if [[ "$PY_OK" != "yes" ]]; then
  echo "ERROR: Python 3.11+ required (3.12/3.13 recommended). Found: $(python3 --version 2>&1)"
  exit 1
fi

PYTHON_BIN="$(command -v python3)"
echo "==> Using $PYTHON_BIN ($(python3 --version 2>&1))"

# ---------------------------------------------------------------------------
# Virtual environment (project-local .venv) — never global pip
# ---------------------------------------------------------------------------
VENV_DIR="$ROOT/.venv"

if [[ -d "$VENV_DIR" && -x "$VENV_DIR/bin/python" ]]; then
  echo "==> Reusing existing virtual environment: $VENV_DIR"
else
  # Fail early with a clear OS-package hint if venv support is missing
  if ! python3 -c "import venv" >/dev/null 2>&1; then
    echo ""
    echo "ERROR: Python's venv module is not available."
    echo "  DevOS installs into a project-local .venv (no global packages)."
    echo "  On Ubuntu/Debian install the OS package that provides venv, e.g.:"
    echo "    sudo apt update && sudo apt install -y python3-venv python3-full"
    echo "  On Ubuntu 24.04+ you may need the versioned package:"
    echo "    sudo apt install -y python3.12-venv"
    echo "  Then re-run: ./install.sh"
    exit 1
  fi

  echo "==> Creating project-local virtual environment: $VENV_DIR"
  if ! python3 -m venv "$VENV_DIR"; then
    echo ""
    echo "ERROR: Failed to create virtual environment at $VENV_DIR."
    echo "  On Ubuntu/Debian try:"
    echo "    sudo apt update && sudo apt install -y python3-venv python3-full"
    echo "  Then re-run: ./install.sh"
    exit 1
  fi
fi

# Always use the venv interpreters from here on (never system pip)
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: Expected executable $VENV_PYTHON after venv creation, but it is missing."
  exit 1
fi

# ---------------------------------------------------------------------------
# Install Python dependencies into the venv only
# ---------------------------------------------------------------------------
REQ="requirements.txt"
if [[ -f "$ROOT/requirements-lite.txt" ]]; then
  REQ="requirements-lite.txt"
fi

echo "==> Installing Python packages from $REQ into .venv"
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PIP" install -r "$ROOT/$REQ"

# ---------------------------------------------------------------------------
# .env — create only if missing; never overwrite an existing .env file
# ---------------------------------------------------------------------------
if [[ ! -f "$ROOT/.env" ]]; then
  if [[ -f "$ROOT/.env.example" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
  else
    printf 'DEBUG=true\nJWT_SECRET=\nDEFAULT_PROVIDER=ollama\nDATABASE_URL=sqlite+aiosqlite:///./data/devos.db\n' > "$ROOT/.env"
  fi
  echo "==> Created .env"
else
  echo "==> .env already exists — leaving it unchanged"
fi

# Fill JWT_SECRET only when missing or empty (does not rewrite other keys)
"$VENV_PYTHON" - <<'PY'
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

# ---------------------------------------------------------------------------
# Prebuilt frontend (required — no Node/npm at runtime)
# ---------------------------------------------------------------------------
if [[ ! -f "$ROOT/frontend/index.html" ]]; then
  echo "ERROR: frontend/index.html missing — this repo should ship a prebuilt UI"
  exit 1
fi
mkdir -p "$ROOT/frontend/templates" "$ROOT/data" "$ROOT/workspace"
if [[ ! -f "$ROOT/frontend/templates/index.html" ]]; then
  cp "$ROOT/frontend/index.html" "$ROOT/frontend/templates/index.html"
fi
if [[ ! -d "$ROOT/frontend/static" ]]; then
  echo "ERROR: frontend/static missing"
  exit 1
fi

# data / workspace: create dirs only; never wipe existing DB or files
mkdir -p "$ROOT/data" "$ROOT/workspace"

# ---------------------------------------------------------------------------
# Root "devos" launcher → always .venv/bin/python cli.py
# ---------------------------------------------------------------------------
LAUNCHER="$ROOT/devos"
cat > "$LAUNCHER" << 'LAUNCHER_EOF'
#!/usr/bin/env bash
# DevOS launcher — always uses the project-local .venv
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: Virtual environment not found at $ROOT/.venv"
  echo "  Run ./install.sh first."
  exit 1
fi
exec "$VENV_PYTHON" "$ROOT/cli.py" "$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
echo "==> Installed launcher: $LAUNCHER"

# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------
echo ""
echo "✅ DevOS installation completed successfully"
echo ""
echo "   Install location : $ROOT"
echo "   Launcher         : ./devos"
echo "   Health check     : ./devos doctor"
echo "   Start web server : ./devos start"
echo "   Open             : http://localhost:8000"
echo ""
echo "   Optional LLM     : install Ollama, or set OPENROUTER_API_KEY in .env"
echo ""
