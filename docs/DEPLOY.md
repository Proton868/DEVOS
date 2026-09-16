# DevOS — reproducible VPS deployment (Ubuntu 24.04)

Assume a **brand-new** Ubuntu 24.04 host. The repository owns install/update/verify.

Postgres/Supabase is the **only** production database authority.  
Nuha orchestrates; specialists execute; Ponytail gates accepted coding output.

## Human-supplied secrets (required)

You must provide (never commit):

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Supabase/Postgres URL (pooler or direct) |
| `JWT_SECRET` | ≥32 character random secret |
| `ALLOWED_ORIGINS` | Public site origin(s), not localhost-only |
| `ADMIN_PASSWORD` | Strong password **or leave empty** to auto-generate |
| `SUPABASE_URL` / `SUPABASE_KEY` | If `AUTH_MODE=dual` or `supabase` |

Optional: `ENCRYPTION_KEY`, OmniRoute reachability at `OMNIROUTE_BASE_URL`, TLS via nginx/Cloudflare.

## Minimal production procedure

```bash
# 1) Base packages
sudo apt update && sudo apt install -y git curl ca-certificates

# 2) Clone
git clone https://github.com/Proton868/DEVOS.git ~/devos
cd ~/devos

# 3) Development bootstrap (creates venv, .env from example)
DEVOS_DEPLOY_MODE=development ./ops/install.sh

# 4) Edit secrets (required human step)
cp -n .env.example .env
$EDITOR .env
# Set at minimum:
#   DEBUG=false
#   REQUIRE_POSTGRES=true
#   DATABASE_URL=postgresql+psycopg://...
#   JWT_SECRET=<strong>
#   ALLOWED_ORIGINS=https://your.domain
#   DEFAULT_PROVIDER=omniroute
#   AUTH_MODE=local   # or dual + SUPABASE_*

# 5) Production install + systemd
DEVOS_DEPLOY_MODE=production DEVOS_INSTALL_SYSTEMD=1 ./ops/install.sh

# 6) Start + verify
sudo systemctl start devos
./ops/verify.sh --production

# 7) (Optional) UI assets if not using prebuilt frontend/
./install.sh   # Node build once, if needed
```

## Ongoing updates

```bash
cd ~/devos
DEVOS_DEPLOY_MODE=production ./ops/update.sh
```

## Repository self-check (no VPS)

```bash
PYTHONPATH=. python scripts/repo_deploy_simulation.py
```

## Lifecycle map (repo coverage)

| Stage | Owned by |
|-------|----------|
| Clone / deps / venv | `ops/install.sh` |
| Env validation | `ops/env_validate.py`, `app._validate_startup_env` |
| Migrations | `scripts/apply_supabase_migrations.py`, `ops/apply_migrations.sh` |
| systemd | `ops/install_systemd.sh`, `ops/templates/devos.service.in` |
| Health | `GET /api/health` |
| Auth | JWT / Supabase dual mode |
| Nuha → specialist → A2A → Ponytail | `api/routes/chat.py`, `brain/a2a.py`, `api/routes/ponytail.py` |
| Outbox / saga | `execution/outbox.py`, `execution/saga.py` |
| Secret redaction | `core/secrets_redact.py` |

## Not automated (intentionally)

- Issuing Supabase project credentials
- DNS / TLS certificates / Cloudflare
- OmniRoute upstream API keys (configured inside OmniRoute, not DevOS)
- Paying cloud resources
