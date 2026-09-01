# DevOS — Agency Operating System

Self-contained AI operating system for human-in-the-loop and autonomous work.

Goals go through a multi-provider **Brain**, **Workers**, **sandboxed execution**, and **UCIP governance** (identity, capabilities, evidence, human-approved autonomy). Defaults use **SQLite** and a **prebuilt web UI** — no Node, Docker, Redis, or cloud DB required to start.

---

## Quick start (2 commands)

```bash
chmod +x install.sh && ./install.sh
python3 cli.py start
```

Open **http://localhost:8000**

That’s it. `install.sh` installs Python deps, creates `.env` with a secure `JWT_SECRET`, and verifies the prebuilt frontend.

| Check | Command |
|-------|---------|
| Health | `python3 cli.py doctor` |
| API health | http://localhost:8000/api/health |
| Chaos drills (optional) | `python scripts/run_chaos_drills.py` |

---

## Requirements

| Need | Minimum |
|------|---------|
| **Python** | **3.11+** (3.12 / 3.13 recommended) |
| **Node** | Not required to run (only to rebuild UI) |
| **Docker** | Optional |
| **Redis / Postgres** | Optional (multi-node / enterprise) |

LLM: local **Ollama** (default) or any configured cloud provider key in `.env`.

---

## What you get

### Governance (v1 — frozen)

```
Identity → UCI/UCIP capability → PathClass → Isolation
         → ExecutionJob (durable) → Evidence → Learning / trust
```

- Workers earn autonomy via competency; **humans approve** promotions  
- Dangerous caps stay human-gated (`shell`, secrets, financial, etc.)  
- Fail-closed trust load and durable job creation  

### Reliability (v1 — frozen)

- Idempotent jobs, lease recovery, secret scrubbing  
- External side effects: **SUCCEEDED / FAILED / UNKNOWN** (no blind retry on UNKNOWN)  
- Chaos pure-logic drills: `python scripts/run_chaos_drills.py`  
- Staging matrix: [docs/STAGING_DRILLS.md](docs/STAGING_DRILLS.md)  

### Product surfaces

| Area | Role |
|------|------|
| Brain + Loop | Plan/execute goals under UCIP |
| Workers | Specialized personas + earned autonomy |
| Scripts / Flow | Sandboxed or human-terminal runs |
| Memory / Evidence | Durable audit and learning |
| Terminal / Files / Git | IDE-style tools in the UI |

---

## Repository layout

```
app.py, cli.py          Entry points
api/routes/             HTTP API
brain/                  LLM router & research
cognitive/              Decomposer, coordinator, Ponytail
workers/                WorkerRuntime + job queue
execution/              Sandbox, scripts, search, terminal
governance/             UCIP, identity, reliability, side effects
chaos/                  Pure-logic chaos drills
memory/                 Memory stores
frontend/               Prebuilt SPA (served as-is)
frontend-src/           React sources (optional rebuild)
scripts/                install helpers, migrate, checklist, chaos
docs/                   Status, staging drills, hardening
```

---

## Configuration

`./install.sh` creates `.env` from [`.env.example`](.env.example).

| Variable | Purpose |
|----------|---------|
| `JWT_SECRET` | Auth + secrets vault seed (auto-generated on install) |
| `DEFAULT_PROVIDER` | `ollama` (default), `openrouter`, `openai`, … |
| `OLLAMA_HOST` | Default `http://127.0.0.1:11434` |
| `DATABASE_URL` | Default SQLite under `./data/` |
| `AUTH_MODE` | `local` / `supabase` / `dual` |
| `REDIS_URL` | Optional multi-node queue/quotas |
| `DEVOS_MULTI_NODE` | `true` → Redis required for expensive quotas |

---

## Other ways to run

**Manual pip**

```bash
cp .env.example .env   # then set JWT_SECRET or run install.sh
pip install -r requirements-lite.txt   # or requirements.txt
python3 cli.py start
```

**Docker**

```bash
docker compose up --build
# http://localhost:8000
```

Profiles (`micro` / `standard` / `enterprise`): see [DEPLOYMENT.md](DEPLOYMENT.md).

**Rebuild UI** (optional)

```bash
cd frontend-src && npm install && npm run build
cd .. && python3 cli.py build
```

---

## CLI

```bash
python3 cli.py start      # API + UI on :8000
python3 cli.py doctor     # environment check
python3 cli.py version
python3 cli.py audit      # UCIP audit tail
```

---

## Tests & quality gates

```bash
# Core unit tests (no live LLM required)
python -m pytest tests/test_governance_freeze.py tests/test_reliability.py \
  tests/test_failure_drills.py tests/test_chaos_harness.py -q

# Chaos pure-logic matrix + report
python scripts/run_chaos_drills.py --out data/chaos/latest_report.json

# Deploy checklist (needs DB; set JWT_SECRET)
python scripts/production_checklist.py
```

---

## Documentation

| Doc | Contents |
|-----|----------|
| **[README.md](README.md)** | Install & overview (this file) |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker profiles, env, production deploy |
| [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) | What is implemented now |
| [docs/STAGING_DRILLS.md](docs/STAGING_DRILLS.md) | Production readiness / chaos matrix |
| [docs/HARDENING.md](docs/HARDENING.md) | Security hardening notes |
| [PRODUCTION_PLAN.md](PRODUCTION_PLAN.md) | Longer production roadmap |
| [plans/AGENCY_OS_MASTER_ARCHITECTURE.md](plans/AGENCY_OS_MASTER_ARCHITECTURE.md) | Architecture reference |

Historical plans under `plans/` and `record.md` may lag the code — prefer **README + CURRENT_STATUS** for “what runs today.”

---

## Design freeze

| Layer | Status |
|-------|--------|
| Governance v1 | Frozen |
| Reliability architecture v1 | Frozen |
| Next focus | Live staging drills on real Postgres/Redis when scaling out |

---

## License

See repository license if present.
