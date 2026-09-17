# DevOS — current status

Aligned with **Governance v1 + Reliability v1** freeze (`main` tip including chaos harness).

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

### VERIFIED (repository / prior operator evidence)
- Commit `8a075fe` direct-psycopg migration runner; 12 unit tests passed
- Prime pulled `8a075fe`; dialect check OK; 13 migration *filenames* skipped via `schema_migrations`
- Prior health: db/memory/orchestration/UCIP/workspace/stores postgres backends OK
- Prior live path: UCIP approved `create_file` before `execution_operations.actor_id` UndefinedColumn

### NOT live-proven (do not claim PASS)
- Production `execution_operations.actor_id` column presence after apply of `20260916210000_...`
- `migrate_agency_schema` completion on Prime
- Current uvicorn bind / `/api/health` after ALLOWED_ORIGINS parse fix
- End-to-end `prime_probe.txt` artifact (content exactly `ok`, 2 bytes)

### Root causes addressed in-repo
1. **Skipped ≠ schema aligned.** Base `20260915130000` used `CREATE TABLE IF NOT EXISTS execution_operations` **without** `actor_id`. Once recorded in `schema_migrations`, re-runs skip the file and never ALTER. New migration `20260916210000_execution_operations_orm_alignment.sql` adds ORM columns with `IF NOT EXISTS`.
2. **ALLOWED_ORIGINS.** Canonical forms: JSON array *or* bare CSV. Production value `[https://dev.carai.agency,http://127.0.0.1:8000]` is invalid JSON; parser now falls back to bracket-stripped CSV so Settings load (and uvicorn) does not crash.
