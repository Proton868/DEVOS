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
./install.sh && python3 cli.py start   # → http://localhost:8000
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
