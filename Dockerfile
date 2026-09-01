# =============================================================================
# CaraiOS Multi-Stage Docker Build — Micro / Standard / Enterprise
# =============================================================================
#
# Build targets:
#   docker build --target micro      -t caraios:micro      .
#   docker build --target standard   -t caraios:standard   .
#   docker build --target enterprise -t caraios:enterprise .
#
# Profile comparison:
#   Micro:      SQLite-only, Ollama assumed external, no Supabase, no pgvector
#   Standard:   SQLite + Supabase optional, pgvector, ChromaDB optional
#   Enterprise: Supabase required, pgvector, Redis, full multi-tenant, RBAC
# =============================================================================

# ── Stage 0: Frontend Build ───────────────────────────────────────────────────
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend-src/package*.json ./
# `npm install` instead of `npm ci`: this repo's lock file is not always in
# sync with package.json. CI will run `npm ci` after a fresh install; local
# Docker builds use this pragmatic fallback.
RUN npm install --omit=dev --no-audit --no-fund
COPY frontend-src/ ./
RUN npm run build

# ── Stage 1: Micro Profile (SQLite-only, zero external deps) ──────────────────
FROM python:3.11-slim AS micro
LABEL org.caraios.profile="micro"
LABEL org.caraios.description="CaraiOS Micro — SQLite-only, no external dependencies"

WORKDIR /app

# Minimal system deps — just enough for Python builds and git
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential git \
    && rm -rf /var/lib/apt/lists/*

# Slim requirements: no supabase, chromadb, redis, or pgvector
COPY requirements-lite.txt .
RUN pip install --no-cache-dir -r requirements-lite.txt

# Copy source
COPY . .

# Frontend build
COPY --from=frontend-build /frontend/build/static/ /app/frontend/static/
COPY --from=frontend-build /frontend/build/index.html /app/frontend/templates/index.html

RUN mkdir -p data/scripts data/venvs data/evidence data/research data/autoresearch

# Create non-root user for security
RUN groupadd -r caraios && useradd -r -g caraios caraios \
    && chown -R caraios:caraios /app/data /app/frontend

ENV CARAIOS_PROFILE=micro
ENV DATABASE_URL=sqlite+aiosqlite:///./data/caraios.db
EXPOSE 8000
USER caraios
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]


# ── Stage 2: Standard Profile (Supabase + ChromaDB optional) ──────────────────
FROM python:3.11-slim AS standard
LABEL org.caraios.profile="standard"
LABEL org.caraios.description="CaraiOS Standard — Supabase optional, ChromaDB optional"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential git curl \
    && rm -rf /var/lib/apt/lists/*

# Standard requirements: includes supabase, chromadb, httpx
COPY requirements-full.txt .
RUN pip install --no-cache-dir -r requirements-full.txt

COPY . .
COPY --from=frontend-build /frontend/build/static/ /app/frontend/static/
COPY --from=frontend-build /frontend/build/index.html /app/frontend/templates/index.html

RUN mkdir -p data/scripts data/venvs data/evidence data/research data/autoresearch

# Create non-root user for security
RUN groupadd -r caraios && useradd -r -g caraios caraios \
    && chown -R caraios:caraios /app/data /app/frontend

ENV CARAIOS_PROFILE=standard
ENV DATABASE_URL=sqlite+aiosqlite:///./data/caraios.db
ENV WEB_CONCURRENCY=2
EXPOSE 8000
USER caraios
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2}"]


# ── Stage 3: Enterprise Profile (full multi-tenant, Redis, pgvector) ──────────
FROM python:3.11-slim AS enterprise
LABEL org.caraios.profile="enterprise"
LABEL org.caraios.description="CaraiOS Enterprise — Full multi-tenant with Redis, pgvector, Supabase"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential git curl postgresql-client redis-tools \
    && rm -rf /var/lib/apt/lists/*

# Enterprise requirements: everything from standard + redis, pgvector, production extras
COPY requirements-full.txt .
RUN pip install --no-cache-dir -r requirements-full.txt

COPY . .
COPY --from=frontend-build /frontend/build/static/ /app/frontend/static/
COPY --from=frontend-build /frontend/build/index.html /app/frontend/templates/index.html

RUN mkdir -p data/scripts data/venvs data/evidence data/research data/autoresearch

# Create non-root user for security
RUN groupadd -r caraios && useradd -r -g caraios caraios \
    && chown -R caraios:caraios /app/data /app/frontend

ENV CARAIOS_PROFILE=enterprise
ENV DATABASE_URL=sqlite+aiosqlite:///./data/caraios.db
ENV WEB_CONCURRENCY=4
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

USER caraios
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-4}"]


# ── Stage 4: Tauri Desktop App Build (optional) ────────────────────────────────
FROM node:20-slim AS tauri-build
LABEL org.caraios.profile="tauri"
LABEL org.caraios.description="CaraiOS Tauri Desktop App Build"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libwebkit2gtk-4.1-dev build-essential curl wget file \
    libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

COPY src-tauri/ /app/src-tauri/
COPY frontend-src/ /app/frontend-src/

WORKDIR /app/src-tauri
RUN npm install -g @tauri-apps/cli && cargo install tauri-cli
CMD ["cargo", "tauri", "build"]
