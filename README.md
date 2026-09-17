# DevOS — Agency Operating System

**Product vision:** [`plans/DEVOS_PRODUCT_VISION.md`](plans/DEVOS_PRODUCT_VISION.md) — unified AI-native Development OS. **Nuha orchestrates; specialist agents execute under UCIP governance.**

Self-contained AI operating system for human-in-the-loop and autonomous engineering work.

Goals flow through:

```
Human → Nuha → Mission / A2A → Specialist Agent → AgentRuntime
  → UCIP / capabilities → Execution → Artifacts → Ponytail → Evidence → Nuha
```

**Production database authority is Postgres/Supabase only** (`REQUIRE_POSTGRES=true`). SQLite is test-isolation only, never production application state. A prebuilt web UI ships under `frontend/`; Node is not required at runtime after install.

**Current tip (docs alignment):** see [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md). Architecture docs under `docs/` describe intended behavior; prefer code + CURRENT_STATUS over lagging plan files.

---

## Production VPS (Ubuntu)

See **[docs/DEPLOY.md](docs/DEPLOY.md)** and **[ops/README.md](ops/README.md)**:

```bash
./ops/install.sh          # venv, deps, migrations (edit .env first)
./ops/update.sh           # git ff-only → deps → migrate → restart → verify
./ops/verify.sh --production
```

Typical host path: `/home/ubuntu/devos` · public URL example: `https://dev.carai.agency`

---

## Quick start

```bash
git clone https://github.com/Proton868/DEVOS.git
cd DEVOS
./install.sh
./devos doctor
./devos start
```

Open **http://localhost:8000**

`install.sh` installs a project-local `.venv`, Python deps, `.env` + `JWT_SECRET`, builds the React UI when needed, and syncs assets into `frontend/static` and `frontend/templates/`.

| Check | Command / URL |
|-------|----------------|
| Health CLI | `./devos doctor` |
| API health | `GET /api/health` |
| Auth public config | `GET /api/auth/public-config` (JSON only — never HTML) |
| Chaos drills | `./.venv/bin/python scripts/run_chaos_drills.py` |

---

## Requirements

| Need | Minimum |
|------|---------|
| **Python** | **3.11+** (3.12 / 3.13 recommended) |
| **Node** | Install-time only (UI build); not required for `./devos start` |
| **Postgres / Supabase** | **Required for production** |
| **SQLite** | Tests only (`REQUIRE_POSTGRES=false` in pytest isolation) |
| **Redis** | Optional (multi-node / enterprise) |
| **Docker** | Optional |

**Default LLM gateway:** OmniRoute (`DEFAULT_PROVIDER=omniroute`). Ollama remains optional.

---

## Authentication (user-facing)

| Layer | Responsibility |
|-------|----------------|
| **Supabase Auth** | Sign-in (email/password; optional OAuth/phone when configured) |
| **DevOS** | Authorization, tenants, roles, UCIP, ownership, IDOR boundaries |
| **`GET /api/auth/public-config`** | Browser-safe `{supabase_url, supabase_anon_key}` only |
| **`POST /api/auth/supabase/sync`** | Verify Supabase JWT server-side → map `User.supabase_id` → issue DevOS JWT |

Flow:

```
Browser → Supabase Auth → access_token
  → POST /api/auth/supabase/sync
  → DevOS User (stable internal id) + tenant/role
  → DevOS JWT (cookie / localStorage) for API calls
```

Environment:

| Variable | Where | Notes |
|----------|-------|--------|
| `SUPABASE_URL` | Server (+ public-config) | Project URL |
| `SUPABASE_ANON_KEY` | Server public-config / `REACT_APP_*` | **Publishable only** |
| `SUPABASE_KEY` | Server only | Never send to browser (may be privileged) |
| `SUPABASE_JWT_SECRET` | Server only | Legacy HS256 projects; modern projects use JWKS |
| `AUTH_MODE` | Server | `dual` (default) · `supabase` · `local` |

Local username/password (`POST /api/auth/login`) remains available in `dual` / `local` modes via an explicit UI toggle — not the primary path when Supabase is configured.

Details: [docs/AUTH_AND_ISOLATION.md](docs/AUTH_AND_ISOLATION.md)

---

## Architecture (code)

```
Identity → UCI/UCIP capability → PathClass → Isolation
         → ExecutionJob / ExecutionOperation → Evidence → HAI cognitive state
```

| Area | Status note |
|------|-------------|
| UCIP + capability registry | Implemented; fail-closed authorization |
| AgentRuntime + HAI control | Implemented; verification-aware completion |
| Consequential operation ledger | Implemented; UNKNOWN is non-retryable |
| Nuha orchestration / A2A | Implemented path; live proof tracked in CURRENT_STATUS |
| Ponytail quality gate | Implemented for agent code acceptance |
| Workflow engine | Durable orchestration; not a second agent runtime |
| SPA routing | `/api/*` never falls through to React `index.html` |

Honest limits and live proof tables: **[docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md)** and **[docs/PRODUCTION_GATES.md](docs/PRODUCTION_GATES.md)**.

### Product surfaces

| Area | Role |
|------|------|
| Nuha (AICopilot) | Orchestrator chat + mission progress (real SSE statuses only) |
| Spatial workspace | IDE (Monaco), workflow canvas, fleet, files, terminal |
| Brain + providers | OmniRoute-native multi-provider LLM |
| Workers / personas | Executable agent registry under governance |
| Memory / graph | Postgres-backed when SoT enforced |
| Evidence / audit | Durable proof and governance trail |

---

## Repository layout

```
app.py, cli.py              Entry points (SPA catch-all excludes /api/*)
api/routes/                 HTTP API (auth, chat, agent, governance, …)
brain/                      AgentRuntime, task store, tools
cognitive/                  HAI, strategic/tactical, Ponytail hooks
workers/                    Job queue / worker runtime
execution/                  Sandbox, durable store, web_intel
governance/                 UCIP, capabilities, evidence, observability
memory/                     Memory + knowledge graph (Postgres SoT)
frontend/                   Prebuilt SPA (served in production)
frontend-src/               React sources
supabase/migrations/        Schema authority (no app-time create_all on Postgres)
ops/                        Install, migrate, systemd, env validate
docs/                       Status, deploy, auth, SoT, gates
tests/                      Pytest suite (isolated SQLite only when allowed)
```

---

## Configuration

`./install.sh` creates `.env` from [`.env.example`](.env.example).

| Variable | Purpose |
|----------|---------|
| `REQUIRE_POSTGRES` | `true` in production (fail-closed) |
| `DATABASE_URL` | Postgres/Supabase URL in production |
| `JWT_SECRET` | Local DevOS JWT + vault seed |
| `AUTH_MODE` | `dual` / `supabase` / `local` |
| `DEFAULT_PROVIDER` | `omniroute` (default) |
| `OMNIROUTE_BASE_URL` | OpenAI-compatible gateway |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | Auth + public-config |
| `SUPABASE_KEY` | Server-side only |
| `ALLOWED_ORIGINS` | CORS (JSON array or CSV; bracketed CSV accepted) |
| `REDIS_URL` | Optional multi-node |

Schema is **migration-owned** (`ops/apply_migrations.sh` → `scripts/apply_supabase_migrations.py`). Production Postgres must not rely on SQLAlchemy `create_all()`.

---

## Other ways to run

**Manual**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-lite.txt   # or requirements.txt
cp -n .env.example .env
./devos start
```

**Docker**

```bash
docker compose up --build
```

**Rebuild UI**

```bash
cd frontend-src
# optional: REACT_APP_SUPABASE_URL / REACT_APP_SUPABASE_ANON_KEY for baked-in client
npm ci && npm run build
```

If build-time env is omitted, the SPA loads Supabase via `GET /api/auth/public-config`.

---

## Documentation map

| Doc | Contents |
|-----|----------|
| [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) | Implemented vs live-proven vs remaining gates |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Production deploy path |
| [docs/AUTH_AND_ISOLATION.md](docs/AUTH_AND_ISOLATION.md) | Auth, tenants, isolation |
| [docs/SUPABASE_SOT.md](docs/SUPABASE_SOT.md) | Postgres single source of truth |
| [docs/PRODUCTION_GATES.md](docs/PRODUCTION_GATES.md) | Release gates |
| [docs/PRODUCTION_OPERATOR_CHECKLIST.md](docs/PRODUCTION_OPERATOR_CHECKLIST.md) | Operator steps on prime |
| [docs/HAI_ARCHITECTURE.md](docs/HAI_ARCHITECTURE.md) | Hierarchical agent intelligence |
| [docs/NUHA_RUNTIME.md](docs/NUHA_RUNTIME.md) | Nuha orchestration |
| [docs/HARDENING.md](docs/HARDENING.md) | Security hardening |
| [docs/STAGING_DRILLS.md](docs/STAGING_DRILLS.md) | Staging / chaos matrix |

Historical plans under `plans/` may lag the code. Prefer **README + CURRENT_STATUS**.

---

## Design freeze

| Layer | Status |
|-------|--------|
| Governance / UCIP contracts | Stable — do not weaken for convenience |
| Reliability (jobs, UNKNOWN, evidence) | Stable |
| Auth dual-mode + Supabase-primary UI | Current |
| Next focus | Live production gates on prime; operator checklist |

## License

DevOS is **source-available** software licensed under the **PolyForm Shield License 1.0.0**.

You may use, study, modify, and distribute DevOS for permitted purposes, subject to the terms of the license.

The license specifically restricts providing products or services that compete with DevOS or with products or services provided by the licensor or its affiliates using DevOS.

DevOS is **not** licensed under an OSI-approved open-source license. The PolyForm Shield License is a source-available license with a noncompete restriction.

| | |
|--|--|
| License | PolyForm Shield 1.0.0 |
| Source available | Yes |
| OSI open source | No |
| Commercial use | Subject to the license |
| Competing products/services | Restricted by the license |

See [LICENSE](./LICENSE) for the complete terms.

See [NOTICE](./NOTICE) for copyright, trademark, and third-party licensing information.

For commercial licensing or uses that may fall within the license's competition restriction, contact **Carai Agency**.

### What this means in practice

You are generally free to:

- run DevOS for your own purposes;
- study the source code;
- modify DevOS;
- create forks and derivative works for permitted purposes;
- use DevOS internally in a business;
- build applications and integrations around DevOS;
- provide professional services involving permitted uses of DevOS.

You may not use DevOS to provide a product or service that competes with DevOS or with a product or service provided by the licensor or its affiliates using DevOS, except where the license expressly permits that use.

The actual legal rights and restrictions are determined by the **PolyForm Shield License 1.0.0**, not by this summary.

If you are unsure whether a proposed commercial use is permitted, obtain a separate commercial license or contact Carai Agency before proceeding.

---

## Trademarks

DevOS, Carai, Carai Agency, Caribbean AI Agency and associated names, logos, product names, service names, and branding are trademarks or protected marks of their respective owners.

The DevOS software license does not grant permission to use these marks to imply endorsement, sponsorship, affiliation, or official status.

You may accurately refer to DevOS as the software used by, based on, or compatible with your project where such use is truthful and does not create a misleading impression of affiliation with Carai Agency.

Forks and derivative projects should use distinct names, logos, and branding and should not present themselves as official versions of DevOS or official Carai products without written permission.

Examples of names that should not be used without authorization include:

- Official Carai DevOS
- Carai DevOS Cloud
- Official DevOS Cloud
- Carai Agency DevOS
- any substantially similar branding that could reasonably imply official sponsorship or affiliation

Nothing in this section grants trademark rights. Trademark rights, if any, are separate from the software license.

See [TRADEMARKS.md](./TRADEMARKS.md) for full guidelines.
