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
| [LICENSE](LICENSE) | PolyForm Shield 1.0.0 (unmodified) |
| [NOTICE](NOTICE) | Copyright & required notice |
| [TRADEMARKS.md](TRADEMARKS.md) | Trademark guidelines |
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

---

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

DevOS, Carai, Carai Agency, Caribbean Ai Agency and associated names, logos, product names, service names, and branding are trademarks or protected marks of their respective owners.

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
