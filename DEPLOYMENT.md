> **Sync note:** Preferred local path is `./install.sh` then `python3 cli.py start`. Docker build targets use **Python 3.13** and **Node 22** for the frontend stage. Runtime UI is the prebuilt `frontend/` directory.

# DevOS Deployment Guide

How to deploy DevOS V4 in production, from a single local container to a
multi-tenant enterprise stack.

---

## Deployment profiles

DevOS ships with three runtime profiles and matching Docker targets.

| Profile | DB | External deps | Use case | Docker target |
|---|---|---|---|---|
| **Micro** | SQLite | None (Ollama can be external) | Personal / air-gapped | `micro` |
| **Standard** | SQLite (+ Supabase optional) | Supabase, optional ChromaDB | Small team / multi-tenant | `standard` |
| **Enterprise** | Postgres/pgvector + Redis | Supabase, Redis, Postgres | Production multi-tenant | `enterprise` |

Profile container: [`Dockerfile`](Dockerfile).  Compose orchestration:
[`docker-compose.yml`](docker-compose.yml).

---

## Environment variables

Copy `.env.example` to `.env` and set at least these:

### Required for every deployment

| Variable | Purpose | Example |
|---|---|---|
| `JWT_SECRET` | HS256 signing key for local tokens; also seeds the Fernet secrets vault | `openssl rand -hex 32` |
| `DATABASE_URL` | SQLAlchemy async URL | `sqlite+aiosqlite:///./data/devos.db` |
| `AUTH_MODE` | `local`, `supabase`, or `dual` | `dual` |
| `ALLOWED_ORIGINS` | CORS allow-list | `https://devos.example.com` |

### Required for Supabase / dual auth

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_KEY` | Public anon key (frontend auth) |
| `SUPABASE_JWT_SECRET` | Legacy HS256 secret; only for pre-2024 projects |

### Required for at least one LLM

| Variable | Provider |
|---|---|
| `OLLAMA_HOST` | Ollama |
| `OPENAI_API_KEY` | OpenAI / Azure-compatible |
| `OPENROUTER_API_KEY` | OpenRouter |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `GEMINI_API_KEY` | Google Gemini |
| `HUGGINGFACE_API_KEY` | HuggingFace router |
| `NARAROUTER_API_KEY` | NaraRouter |

Set `DEFAULT_PROVIDER` to one of: `ollama`, `openai`, `openrouter`, `deepseek`,
`gemini`, `huggingface`, `nararouter`.

### Optional integrations

- `TAVILY_API_KEY` / `SEARXNG_URL` — web search
- `NTPY_URL` — push notifications
- `SMTP_*` / `TELEGRAM_*` — notification channels
- `CHROMADB_HOST` / `CHROMADB_PORT` — external Chroma vector store

---

## Container deployment

### Micro (single container)

```bash
docker build --target micro -t devos:micro .
docker run -d \
  --name devos \
  -p 8000:8000 \
  -v devos_data:/app/data \
  -e JWT_SECRET="$(openssl rand -hex 32)" \
  -e ADMIN_PASSWORD="$(openssl rand -base64 24)" \
  devos:micro
```

### Standard / Compose

```bash
cp .env.example .env
# edit .env: SUPABASE_URL, SUPABASE_KEY, JWT_SECRET, ADMIN_PASSWORD
docker compose --profile standard up -d
```

### Enterprise / Compose

```bash
docker compose --profile enterprise up -d
```

The enterprise profile adds Redis and expects either a managed Postgres or the
Postgres service referenced in `docker-compose.yml`.

### Health check

All profiles expose:

```
GET /api/health
```

Used by both the Docker `HEALTHCHECK` and compose `healthcheck`. The endpoint
returns a 200 with a `db` field; if the database is unreachable the status is
`degraded`.

---

## Reverse proxy / TLS

Put DevOS behind a reverse proxy that terminates TLS and forwards `X-Forwarded-*`
headers. Example Caddyfile:

```
devos.example.com {
    reverse_proxy localhost:8000 {
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}
    }
}
```

Nginx equivalent is in [`frontend-src/nginx.conf`](frontend-src/nginx.conf); adapt
it to proxy the backend instead of serving static files if you use Nginx as the
primary edge server.

---

## Frontend rebuilds

The production frontend is the static build in `frontend/build/`. After editing
React sources in `frontend-src/`:

```bash
python3 cli.py build
```

or directly:

```bash
cd frontend-src && npm ci && npm run build
```

Then restart the server or rebuild the container so it copies the new
`frontend/build/` assets.

---

## Security checklist

Before exposing DevOS to the internet:

- [ ] Set a strong, persistent `JWT_SECRET`.  Changing it invalidates all sessions
      and makes stored secrets undecryptable.
- [ ] Set a strong `ADMIN_PASSWORD` and rotate it after first login.
- [ ] Run over HTTPS only; set `ALLOWED_ORIGINS` to the production domain(s).
- [ ] If using Supabase, confirm `AUTH_MODE` is `dual` during migration, then
      switch to `supabase` once all admin identities are synced.
- [ ] Restrict filesystem access: DevOS writes to `data/`, `workspace/`, and
      sandbox temp dirs. Run the container with a read-only root filesystem and
      volume mounts for the writable paths if possible.
- [ ] For untrusted code execution, deploy with a sandbox runtime
      (gVisor/Firecracker/Docker with seccomp). The built-in
      [`SandboxedExecutor`](governance/sandbox.py) provides process/resource
      limits, but it runs in the same kernel as the host.
- [ ] Add a Content-Security-Policy and security headers at the reverse proxy or
      in FastAPI middleware ( helmet-equivalent ).
- [ ] Enable rate limiting and audit logging; review
      [`governance/audit.py`](governance/audit.py) events regularly.
- [ ] Back up `data/` and the `JWT_SECRET` together; secrets are encrypted with
      a key derived from `JWT_SECRET`, so losing it means losing stored secrets.
- [ ] Confirm `ENABLE_API_DOCS` is false (or `DEBUG` is false) before exposing to
      the internet, unless API docs are intentionally meant to be public.

---

## Updating DevOS

```bash
git pull
docker compose --profile standard down
docker compose --profile standard build --no-cache
docker compose --profile standard up -d
```

Database schema migrations are handled automatically on startup by
[`core/database.py`](core/database.py) using `Base.metadata.create_all()` plus a
small PRAGMA-based column migration for SQLite. For Postgres enterprise
deployments, apply migrations manually or with a tool such as Alembic.

---

## Terminal (PTY-backed interactive shell)

The terminal is now a persistent, PTY-backed interactive shell — one session per
project, shared across all browser tabs or connections attached to that project
at once. This replaces the previous one-command-at-a-time model.

**Key behaviors:**
- **Multi-client**: Multiple WebSocket connections to the same project share the
  same PTY. Input from any client is accepted; output is broadcast to all.
- **Scrollback**: 256KB rolling buffer replayed to reconnecting clients.
- **Idle timeout**: 30 minutes with no attached connections and no output kills
  the shell process (configurable via `PTY_IDLE_TIMEOUT` in .env).
- **Resource limits**: CPU (5 min), memory (512MB), and file descriptor (256)
  caps applied via `resource.prlimit` to the spawned shell process.
- **Safety**: The command-pattern denylist from the old one-command model is
  intentionally dropped for interactive mode — input arrives as individual
  keystrokes, not discrete commands. Protection relies on: project-directory
  confinement, resource limits, and non-root execution. The container runs as
  user `caraios` (see the Dockerfile's `USER` directive), so the shell cannot
  access host-level resources even without a denylist regex.
- **Interactive apps**: Supports `vim`, `htop`, `python3` REPL, and other
  curses-based programs via xterm.js rendering.

**Project isolation**: Two different projects get fully separate PTY sessions
and cannot see or affect each other's shell state, working directory, or
environment.

**Cleanup**: Deleting a project kills any active PTY session for it. On app
shutdown, all active PTY sessions are killed.

---

## Multi-worker known limitations

When `WEB_CONCURRENCY > 1`, uvicorn spawns multiple worker processes, each with
its own memory space. The following in-memory singletons are **not shared** across
workers:

| Component | Module | Limitation |
|---|---|---|
| HITL queue | `governance/hitl.py` | Pending approval requests are only visible to the worker that received them |
| EventBus | `communications/bus.py` | Events published on one worker are not received by subscribers on another |
| Rate limiter | `governance/ratelimit.py` | Rate-limit counters are per-worker, not global |
| Workflow engine | `brain/workflow.py` | Workflow definitions are stored in-memory, per-worker |
| PTY sessions | `execution/pty_session.py` | PTY sessions live on whichever worker accepted the WebSocket |

**For single-user/single-worker deployments (Micro profile, `WEB_CONCURRENCY=1`):**
these are non-issues — all state lives in one process.

**For multi-worker deployments (Standard/Enterprise):** moving HITL queue,
EventBus, and rate limiter state to Redis is planned but not yet implemented.
Until then, keep `WEB_CONCURRENCY=1` if you depend on cross-request shared state
(HITL approvals, EventBus subscriptions, rate limiting across workers).

---

## Email notifications

Email delivery uses the `aiosmtplib` library. Configure SMTP in `.env`:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=user@example.com
SMTP_PASSWORD=your_password
SMTP_FROM=devos@example.com
```

When SMTP is not configured, `send_email()` raises `NotConfiguredError` so
callers (workflow NOTIFY steps, automation templates) can surface "email isn't
configured" clearly rather than a confusing failure or silent no-op.

See [`communications/email.py`](communications/email.py) for the module.

---

## Backup and restore

Automated backup and restore scripts are at [`scripts/backup.py`](scripts/backup.py)
and [`scripts/restore.py`](scripts/restore.py).

**Backup:**
```bash
python3 scripts/backup.py                     # Default: ./backups/
python3 scripts/backup.py -o /mnt/backups     # Custom output dir
python3 scripts/backup.py --keep-last 7       # Keep last 7 backups only
```

**Restore:**
```bash
python3 scripts/restore.py devos-backup-20260101-120000.tar.gz
python3 scripts/restore.py devos-backup-20260101-120000.tar.gz --yes  # Non-interactive
```

**What's backed up:** Database dump (SQLite `.dump` or Postgres `pg_dump`),
`data/evidence/` files, and the encrypted secrets database.

**What's NOT backed up:** `JWT_SECRET` / `.env` file. Secrets are encrypted with
a key derived from `JWT_SECRET`, so losing `JWT_SECRET` means losing stored
secrets. Back up `.env` separately.

**Automation:** Wire as a cron job:
```cron
0 3 * * * cd /path/to/devos && python3 scripts/backup.py -o /mnt/backups --keep-last 7
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 Invalid token` after restart | `JWT_SECRET` changed; if unset, DevOS persists one in `core/.devos_secret` — copy that into `.env`. |
| `503 All LLM providers failed` | No provider keys are set or Ollama is unreachable. Set keys or check `OLLAMA_HOST`. |
| Scripts run but produce no output | Check `SCRIPT_TIMEOUT` and the script language runtime inside the container. |
| Webhook URLs 404 | Webhook tokens are URL-safe but not paths; POST to `/api/scripts/webhook/{token}`. |
| PTY terminal not accepting input | Check that `currentProject` is set in the frontend store; the WebSocket URL includes the project ID. |
| Email not sending | SMTP settings are not configured; `send_email()` raises `NotConfiguredError` — check `.env` for `SMTP_HOST`.
