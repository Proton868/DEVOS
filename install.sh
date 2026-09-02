#!/usr/bin/env bash
# DevOS — single-command complete installation
# Handles: Python venv, Python deps, .env, Node 22, frontend npm ci + build,
# runtime frontend asset sync. No separate npm/build step required by the user.
# Safe on Ubuntu 24.04+ with PEP 668. Idempotent: preserves .env, data/, workspace/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

NODE_MAJOR_REQUIRED=22
NODE_INSTALL_DIR="${ROOT}/.tools/node"

echo "==> DevOS installer (complete single-command setup)"
echo "    Location: $ROOT"

have_cmd() { command -v "$1" >/dev/null 2>&1; }

node_major() {
  local v
  v="$(node --version 2>/dev/null || true)"
  v="${v#v}"
  echo "${v%%.*}"
}

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
if ! have_cmd python3; then
  echo "ERROR: python3 is required (3.11+)."
  exit 1
fi

PY_OK="$(python3 -c 'import sys; print("yes" if sys.version_info >= (3, 11) else "no")')"
if [[ "$PY_OK" != "yes" ]]; then
  echo "ERROR: Python 3.11+ required (3.12/3.13 recommended). Found: $(python3 --version 2>&1)"
  exit 1
fi
echo "==> Using $(command -v python3) ($(python3 --version 2>&1))"

VENV_DIR="$ROOT/.venv"
if [[ -d "$VENV_DIR" && -x "$VENV_DIR/bin/python" ]]; then
  echo "==> Reusing existing virtual environment: $VENV_DIR"
else
  if ! python3 -c "import venv" >/dev/null 2>&1; then
    echo ""
    echo "ERROR: Python's venv module is not available."
    echo "  On Ubuntu/Debian:"
    echo "    sudo apt update && sudo apt install -y python3-venv python3-full"
    echo "  On Ubuntu 24.04+ you may need:"
    echo "    sudo apt install -y python3.12-venv"
    echo "  Then re-run: ./install.sh"
    exit 1
  fi
  echo "==> Creating project-local virtual environment: $VENV_DIR"
  if ! python3 -m venv "$VENV_DIR"; then
    echo "ERROR: Failed to create virtual environment at $VENV_DIR."
    echo "  Try: sudo apt install -y python3-venv python3-full"
    exit 1
  fi
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: Expected executable $VENV_PYTHON after venv creation."
  exit 1
fi

REQ="requirements.txt"
if [[ -f "$ROOT/requirements-lite.txt" ]]; then
  REQ="requirements-lite.txt"
fi
echo "==> Installing Python packages from $REQ into .venv"
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PIP" install -r "$ROOT/$REQ"

# ---------------------------------------------------------------------------
# .env
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

mkdir -p "$ROOT/data" "$ROOT/workspace" "$ROOT/frontend/templates" "$ROOT/frontend/static"

# ---------------------------------------------------------------------------
# Node 22
# ---------------------------------------------------------------------------
ensure_node22() {
  local major=""
  if have_cmd node; then
    major="$(node_major)"
    if [[ "$major" == "$NODE_MAJOR_REQUIRED" ]]; then
      echo "==> Using system Node $(node --version) / npm $(npm --version 2>/dev/null || echo '?')"
      return 0
    fi
    echo "==> System Node is v${major:-unknown}; need Node ${NODE_MAJOR_REQUIRED}.x for frontend build"
  else
    echo "==> Node.js not found; will provision Node ${NODE_MAJOR_REQUIRED} LTS locally"
  fi

  if [[ -x "$NODE_INSTALL_DIR/bin/node" ]]; then
    export PATH="$NODE_INSTALL_DIR/bin:$PATH"
    major="$(node_major)"
    if [[ "$major" == "$NODE_MAJOR_REQUIRED" ]]; then
      echo "==> Using project-local Node $(node --version)"
      return 0
    fi
  fi

  local arch os_name tarball url tmpdir ver
  os_name="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      echo "ERROR: Unsupported architecture $(uname -m) for automatic Node install."
      echo "  Install Node ${NODE_MAJOR_REQUIRED} LTS manually, then re-run ./install.sh"
      exit 1
      ;;
  esac

  echo "==> Downloading Node.js ${NODE_MAJOR_REQUIRED} LTS (${os_name}-${arch})..."
  tmpdir="$(mktemp -d)"
  ver="22.14.0"
  if have_cmd curl; then
    local latest
    latest="$(curl -fsSL "https://nodejs.org/dist/index.json" 2>/dev/null \
      | python3 -c "import sys,json; rels=json.load(sys.stdin); print(next((r['version'].lstrip('v') for r in rels if r['version'].startswith('v22.')), ''))" \
      2>/dev/null || true)"
    [[ -n "$latest" ]] && ver="$latest"
  fi

  tarball="node-v${ver}-${os_name}-${arch}.tar.xz"
  url="https://nodejs.org/dist/v${ver}/${tarball}"

  if ! curl -fsSL "$url" -o "${tmpdir}/${tarball}"; then
    echo "ERROR: Failed to download Node.js from $url"
    echo "  Install Node ${NODE_MAJOR_REQUIRED} LTS manually (https://nodejs.org/), then re-run ./install.sh"
    rm -rf "$tmpdir"
    exit 1
  fi

  tar -xJf "${tmpdir}/${tarball}" -C "$tmpdir"
  local extracted
  extracted="$(find "$tmpdir" -maxdepth 1 -type d -name 'node-v*' | head -1)"
  if [[ -z "$extracted" ]]; then
    echo "ERROR: Failed to extract Node.js tarball."
    rm -rf "$tmpdir"
    exit 1
  fi
  rm -rf "$NODE_INSTALL_DIR"
  mkdir -p "$(dirname "$NODE_INSTALL_DIR")"
  mv "$extracted" "$NODE_INSTALL_DIR"
  rm -rf "$tmpdir"

  export PATH="$NODE_INSTALL_DIR/bin:$PATH"
  if [[ "$(node_major)" != "$NODE_MAJOR_REQUIRED" ]]; then
    echo "ERROR: Provisioned Node but version check failed: $(node --version 2>&1)"
    exit 1
  fi
  echo "==> Provisioned project-local Node $(node --version) / npm $(npm --version)"
}

ensure_node22

if ! have_cmd npm; then
  echo "ERROR: npm not found after Node provisioning."
  exit 1
fi

# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------
FRONTEND_SRC="$ROOT/frontend-src"
if [[ ! -f "$FRONTEND_SRC/package.json" ]]; then
  echo "ERROR: frontend-src/package.json missing."
  exit 1
fi
if [[ ! -f "$FRONTEND_SRC/package-lock.json" ]]; then
  echo "ERROR: frontend-src/package-lock.json missing. Cannot run deterministic npm ci."
  exit 1
fi

echo "==> Installing frontend dependencies (npm ci) in frontend-src/"
(
  cd "$FRONTEND_SRC"
  npm_config_registry="${npm_config_registry:-https://registry.npmjs.org/}" \
    npm ci --no-audit --no-fund
)

echo "==> Building frontend (npm run build)"
(
  cd "$FRONTEND_SRC"
  CI=true npm_config_registry="${npm_config_registry:-https://registry.npmjs.org/}" \
    npm run build
)

BUILD_DIR="$FRONTEND_SRC/build"
if [[ ! -f "$BUILD_DIR/index.html" ]] || [[ ! -d "$BUILD_DIR/static" ]]; then
  echo "ERROR: Frontend build did not produce build/index.html and build/static/."
  exit 1
fi

echo "==> Synchronizing runtime frontend assets → frontend/"
mkdir -p "$ROOT/frontend/templates" "$ROOT/frontend/static"
rm -rf "$ROOT/frontend/static"
cp -a "$BUILD_DIR/static" "$ROOT/frontend/static"
cp "$BUILD_DIR/index.html" "$ROOT/frontend/index.html"
cp "$BUILD_DIR/index.html" "$ROOT/frontend/templates/index.html"
if [[ -f "$BUILD_DIR/asset-manifest.json" ]]; then
  cp "$BUILD_DIR/asset-manifest.json" "$ROOT/frontend/asset-manifest.json"
fi

"$VENV_PYTHON" - <<'PY'
from pathlib import Path
import re, sys
root = Path(".")
html = (root / "frontend/templates/index.html").read_text()
refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
missing = [r for r in refs if not (root / "frontend" / r.lstrip("/")).exists()]
if missing:
    print("ERROR: frontend HTML references missing assets:")
    for m in missing:
        print(f"  - {m}")
    sys.exit(1)
print(f"==> Frontend assets OK ({len(refs)} referenced static files present)")
PY

# ---------------------------------------------------------------------------
# Launcher
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

echo ""
echo "✅ DevOS installation completed successfully"
echo ""
echo "   Install location : $ROOT"
echo "   Launcher         : ./devos"
echo "   Health check     : ./devos doctor"
echo "   Start web server : ./devos start"
echo "   Open             : http://localhost:8000"
echo ""
echo "   Note: Node/npm were used only to build the frontend."
echo "   They are NOT required to run ./devos start after installation."
echo ""
echo "   Optional LLM     : install Ollama, or set OPENROUTER_API_KEY in .env"
echo ""
