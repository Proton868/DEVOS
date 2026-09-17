# DevOS — current status

**Docs aligned to git tip including:** `e8b6fa3` (SPA `/api/*` JSON), Supabase Auth login (`5fa048b` / `119dc6e`), Nuha progress UI (`04a736c`), execution_operations `actor_id` (`38949b5`).

**Labels used below:** VERIFIED LOCAL · VERIFIED PRIME LIVE · NOT VERIFIED

---

## Install (self-contained)

| Item | Status |
|------|--------|
| `./install.sh` | One step: project-local `.venv` + deps + `.env` + JWT + frontend |
| Prebuilt `frontend/` | No Node required to run |
| Default DB | SQLite (`./data/devos.db`) |
| Docker | Optional (`docker compose up --build`) |
| Redis / Postgres | Optional (multi-node / enterprise) |

```bash
./install.sh && ./devos start   # → http://localhost:8000
```

## Backend capabilities

| Area | Status |
|------|--------|
| FastAPI (`app.py`) + CLI | ✓ |
| Auth local / supabase / dual | ✓ |
| Brain multi-provider LLM | ✓ |
| UCIP gateway + capability registry | ✓ (includes `ucip:package.install`) |
| Workers + earned autonomy (human-gated promotion) | ✓ |
| Execution pipeline (PathClass, jobs, evidence) | ✓ |
| Sandbox isolation + fail-closed trust | ✓ |
| Scripts (durable job + evidence parity) | ✓ |
| Marketplace install (governed) | ✓ |
| Autoresearch (job + authority envelope) | ✓ |
| Job queue: idempotency, leases, recovery | ✓ |
| Side effects: SUCCEEDED / FAILED / UNKNOWN | ✓ |
| Secret scrubbing on durable payloads | ✓ |
| Chaos pure-logic drills | ✓ `scripts/run_chaos_drills.py` |
| Production checklist gate | ✓ `scripts/production_checklist.py` |

## Frontend

| Area | Status |
|------|--------|
| Prebuilt SPA in `frontend/` | ✓ |
| Automation Hub (Graph + Matrix) | ✓ |
| IDE dock, agents, files, terminal | ✓ |
| Onboarding wizard | ✓ |

## Honest limits

- LLM quality depends on Ollama or cloud keys (not exercised in CI without keys).
- Pure-logic chaos drills **do not** replace live Postgres/Redis/process-kill staging.
- Multi-node quotas need Redis when `DEVOS_MULTI_NODE=true`.
- Plan docs under `plans/` may lag; this file + README are authoritative for “what runs.”

## Operator path

1. `./install.sh`
2. Optional: start Ollama or set a provider key in `.env`
3. `./devos start`
4. Open http://localhost:8000
5. `./devos doctor` if issues

## Cluster 4 — migration / ALLOWED_ORIGINS / actor_id (2026-09-16)

**Status: FULLY LIVE-PROVEN on Prime** (do not downgrade to “tests only”).

### 1. VERIFIED LOCAL TESTS

| Suite | Result |
|-------|--------|
| `pytest tests/test_apply_supabase_migrations_runner.py -q` | **12 passed** |
| `pytest tests/test_allowed_origins_bracketed_csv.py tests/test_execution_operations_schema_migration.py -q` | **8 passed** |

Implementation commits:

- `8a075fe` — direct psycopg migration runner
- `38949b5` (`38949b5703a949d6d590088152ea134bdc7df19f`) — `execution_operations.actor_id` alignment + bracketed CORS

### 2. VERIFIED PRIME LIVE PROOF

Live PostgreSQL:

```
FOUND COLUMNS:
('actor_id', 'text', 'YES')
('status', 'text', 'NO')
PASS: execution_operations.actor_id EXISTS IN LIVE POSTGRES
```

DevOS restart + health (`dev.carai.agency`):

- `service=devos` `status=ok`
- `db=ok` `db_backend=postgres`
- `memory=ok` `memory_backend=postgres`
- `governance=v1-frozen` `ucip=ok` `workspace=import_ok`
- `execution_store_backend=postgres` `outbox_backend=postgres`
- `saga_backend=postgres` `audit_backend=postgres`

Authenticated:

- `GET /api/auth/me` → **200**
- identity: `username=cluster4_probe` `role=member` `plan=recruit`

Real mission:

- `POST /api/chat/send` intent: create `prime_probe.txt` with exact content `ok`
- HTTP **200** accepted on server
- Client Python reader later **timed out** waiting on the streamed body — **not** a mission failure; server continued

UCIP (server logs):

- `[UCIP:APPROVE] action=search_files cap=ucip:filesystem.read`
- `[UCIP:APPROVE] action=create_file cap=ucip:filesystem.write reason=policy check passed`
- **PASS:** no `actor_id` UndefinedColumn / schema error

Artifact:

- Path: `/home/ubuntu/devos/data/projects/358d6f79-f55c-473e-b2c1-f2f37b9c9509/default/prime_probe.txt`
- **EXISTS**, **BYTES: 2**, **HEX: `6f 6b`**, **CONTENT: `b'ok'`** (no newline)

### 3. NOT YET VERIFIED / REMAINING RELEASE GATES

Cluster 4 does **not** mean the full product is release-green. Remaining examples:

- Full multi-tenant production load / long-lived SSE client resilience under all proxies
- Cluster 5 Nuha progress UI live visual confirmation on Prime
- Broader E2E matrix beyond the single-file probe
- Any gate listed in `docs/PRODUCTION_GATES.md` not re-run after this tip

---

## Cluster 5 — Nuha task execution progress UI (2026-09-16)

**Scope:** Truthful mission progress UI from real `/api/chat` SSE statuses; docs accuracy.

### Behavior

- Progress panel maps only known SSE statuses: `planning`, `plan_created`, `delegating`, `agent_progress` / `worker_completed`, `validation_*`, `artifact_created`, plus terminal `completed` / `failed` / `cancelled`
- No invented percentages, fake stages, or fabricated elapsed time without a real stream start timestamp
- `streamChat` accepts `AbortSignal`, cancels the reader on abort, flushes trailing SSE buffer, reports `stream_state`: `closed` | `aborted` | `error`
- Stream abort/close is **not** treated as mission success

### 1. VERIFIED LOCAL TESTS

| Command | Result |
|---------|--------|
| `node frontend-src/scripts/test-nuha-task-progress.mjs` | progress logic assertions |

### 2. LIVE PRIME VERIFICATION

**Cluster 5 live Prime verification remains unproven** (repository-only work in this pass).

### 3. Remaining

- Confirm progress panel on `dev.carai.agency` during a real chat mission
- Optional: rebuild/deploy frontend static assets on Prime after pull

## Authentication — Supabase Auth user-facing login (2026-09-16)

### Architecture

| Layer | Responsibility |
|-------|----------------|
| **Supabase Auth** | User authentication (email/password, optional OAuth/phone) |
| **DevOS** | Authorization, tenants, roles, UCIP, ownership, governance |
| **`/api/auth/supabase/sync`** | Validate Supabase JWT (JWKS/legacy), map `sub`→`User.supabase_id`, issue DevOS JWT |
| **`/api/auth/public-config`** | Browser-safe `{supabase_url, supabase_anon_key}` only — never service_role |

Identity flow:

```
Browser → Supabase Auth → access_token
  → POST /api/auth/supabase/sync (server verifies JWT)
  → DevOS User (existing id preserved; link by supabase_id then email)
  → DevOS JWT (cookie + localStorage) for API Authorization
```

Local username/password (`POST /api/auth/login`) remains available when `AUTH_MODE` is `dual` or `local` via an explicit UI toggle — not the primary path when Supabase is configured.

### Account linking

1. Match `User.supabase_id` to JWT `sub`
2. Else match `User.email` (deterministic link of pre-provisioned accounts)
3. Else create User with `hashed_password=None` (Supabase-only)

Never trust browser-supplied user_id / tenant_id / role for authorization.

### Security

- Frontend uses **anon/publishable** key only (`REACT_APP_SUPABASE_ANON_KEY` or public-config)
- `SUPABASE_KEY` / service_role never returned by public-config
- JWT verified server-side (JWKS RS256/ES256 or `SUPABASE_JWT_SECRET` HS256)
- `user_metadata` is not an authorization source

### Verification status

| Gate | Status |
|------|--------|
| Repository tests (`test_supabase_auth_login`, `test_auth_mode`, …) | LOCAL |
| Frontend production build | LOCAL (if run) |
| Prime deploy | **NOT VERIFIED** in this pass |
| https://dev.carai.agency live login | **NOT VERIFIED** in this pass |

