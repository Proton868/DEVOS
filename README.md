# DevOS — Agency Operating System

Production-oriented AI OS for autonomous and human-in-the-loop work. Goals route through a multi-provider Brain, specialized workers, sandboxed execution, and UCIP governance (identity, RBAC, audit, approvals).

---

## Supported runtimes

| Runtime | Minimum | Recommended / Docker / CI |
|---------|---------|---------------------------|
| **Python** | 3.11 | **3.13** (3.12 fine) |
| **Node** (UI *rebuild* only) | 20 | **22 LTS** |

**Running the app does not require Node** — releases ship a prebuilt `frontend/` tree.

---

## Install (one step)

```bash
chmod +x install.sh && ./install.sh
python3 cli.py start
```

Open **http://localhost:8000**

- First launch shows a **setup wizard** (Skip / Finish; “Don’t show again” by default).
- Replay: Settings → UI → Replay setup wizard (when available), or clear `localStorage.devos_onboarded`.

Optional Docker:

```bash
docker compose --profile micro up --build
# Images use python:3.13-slim; frontend build stage uses node:22-slim
```

Manual alternative:

```bash
cp .env.example .env   # set JWT_SECRET for non-DEBUG
pip install -r requirements-lite.txt   # or requirements.txt
python3 cli.py start
```

---

## What ships in this tree

| Area | Path | Role |
|------|------|------|
| App entry | `app.py`, `cli.py` | FastAPI app + CLI (`start`, `build`, `doctor`, …) |
| Brain | `brain/` | LLM router, research, workflow definitions, builder |
| Cognitive | `cognitive/` | Intent, decomposer, reflector, Ponytail pipeline |
| Workers | `workers/` | Persona runtime / capability dispatch |
| Execution | `execution/` | Sandboxed code, files, terminal helpers |
| Governance | `governance/` | UCIP, RBAC, audit, billing, secrets |
| Memory | `memory/` | Episodic / semantic / working stores |
| API | `api/routes/` | Auth, chat, scripts, workflow, research, workers, … |
| UI source | `frontend-src/` | React (Midnight Obsidian + Neon Mint) |
| UI **runtime** | `frontend/` | Prebuilt SPA served at `/` and `/static` |
| Install | `install.sh` | Single-step Python install + UI check |

### Main API surface (prefix `/api`)

Auth, chat, loop, scripts (PyRunner), memory, search, models, health, governance, files, vcs, terminal, workers, secrets, capabilities, evidence, research, ponytail, workflows, enterprise, mcp, marketplace, composer, user settings, nodes.

### Frontend highlights

- **Flow / Automation Hub** — Graph (workflow canvas) + PyRunner Matrix  
- **DevOS IDE** (right dock) — editor + AI chat  
- **Agents, Files, Terminal, Git** — left-rail workspaces  
- **Onboarding wizard** — first-run tour of these surfaces  

---

## Authentication

| `AUTH_MODE` | Behavior |
|-------------|----------|
| `local` | bcrypt + HS256 JWT |
| `supabase` | Supabase tokens |
| `dual` (default) | Local JWT, then Supabase |

Bootstrap admin is printed on first boot when using local/dual mode. See `core/config.py` and `api/routes/auth.py`.

---

## Configuration

Copy `.env.example` → `.env`. Important keys:

- `JWT_SECRET` — required when `DEBUG=false`
- `DEFAULT_PROVIDER` — e.g. `ollama`, `openrouter`, `openai`
- Provider keys: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …
- `OLLAMA_HOST` — default `http://127.0.0.1:11434`
- `ENABLE_API_DOCS` — OpenAPI UI when true

```bash
python3 cli.py doctor    # environment check
```

---

## Developing the UI

Not required to run the product:

```bash
cd frontend-src
npm install          # Node 20+ / 22 recommended
npm run build
cd .. && python3 cli.py build   # sync into frontend/
```

---

## Research naming

Deep multi-step research in this codebase is the **Hermes-style** research path (`brain/research.py` and related routes). Older “Odysseus” wording in historical notes refers to prior naming; runtime and current docs use **Hermes / research** APIs.

---

## Docs map

| Doc | Purpose |
|-----|---------|
| [README.md](README.md) | This file — install & overview |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deploy profiles, env, reverse proxy |
| [PRODUCTION_PLAN.md](PRODUCTION_PLAN.md) | Staged production roadmap & status |
| [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) | Snapshot of what is implemented now |
| [plans/AGENCY_OS_MASTER_ARCHITECTURE.md](plans/AGENCY_OS_MASTER_ARCHITECTURE.md) | Architecture reference |
| [plans/GAP_ANALYSIS.md](plans/GAP_ANALYSIS.md) | Historical gap notes (may lag code) |

---

## License / project

See repository license file if present. Upstream development tracked against the Agency OS organ map (cognitive, workers, memory, governance, workflow, packaging).
