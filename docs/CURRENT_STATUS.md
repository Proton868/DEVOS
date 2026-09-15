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
| Spatial OS workspace (canvas-primary shell) | ✓ |
| Workflow orchestration canvas (real graph over `/api/scripts` + chains) | ✓ |
| DevOS IDE — ephemeral Monaco editor | ✓ |
| AI Copilot (contextual, project/node/file-aware) | ✓ |
| Ghost Terminal (PyRunner runtime, real log streaming) | ✓ |
| Agency Dashboard (live agent-fleet HUD) | ✓ |
| Command bar (CMD+K / CTRL+K / SPACE) | ✓ |
| Files / Git / Search / Memory / MCP / Research / Composer / Settings overlays | ✓ |
| Supabase + local login | ✓ |

## Nuha runtime (canonical)

Authoritative spine (see [NUHA_RUNTIME.md](NUHA_RUNTIME.md)):

`Nuha → Mission → DAG → UCIP → AgentRuntime → Tools → Artifacts → Verification → Mission Truth → SSE`

| Item | Status |
|------|--------|
| `mission_truth()` status → ok / synthesis_mode | IMPLEMENTED + TESTED |
| `MISSION_EXECUTION` vs `DIRECT_SCAFFOLD` labels | IMPLEMENTED |
| Node COMPLETED requires verification pass | IMPLEMENTED |
| Agent-only `files_changed` without on-disk proof | does **not** pass verification |
| Live SSE / cancel / HITL on production host | **UNPROVEN** |

Production gates: [PRODUCTION_GATES.md](PRODUCTION_GATES.md) · Runtime: [NUHA_RUNTIME.md](NUHA_RUNTIME.md)

## Honest limits


- LLM quality depends on OmniRoute (default gateway) and its upstream models; Ollama/cloud keys are optional direct providers.
- Pure-logic chaos drills **do not** replace live Postgres/Redis/process-kill staging.
- Multi-node quotas need Redis when `DEVOS_MULTI_NODE=true`.
- Plan docs under `plans/` may lag; this file + README are authoritative for “what runs.”

## Operator path

1. `./install.sh`
2. Ensure OmniRoute is running locally (`OMNIROUTE_BASE_URL`); Ollama/cloud keys remain optional
3. `./devos start`
4. Open http://localhost:8000
5. `./devos doctor` if issues
