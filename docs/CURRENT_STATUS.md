# DevOS — current codebase status

_Last aligned with the tree that ships install.sh + prebuilt `frontend/` + Python 3.13 / Node 22 tooling targets._

## Install & packaging

- [x] Single-step `./install.sh` (Python deps + `.env` + prebuilt UI check)
- [x] Prebuilt SPA in `frontend/` (no Node required to run)
- [x] Docker multi-stage: `python:3.13-slim`, frontend build `node:22-slim`
- [x] CLI: `python3 cli.py start|build|doctor|…`

## Backend

- [x] FastAPI app (`app.py`) serves `/api/*` and SPA fallback
- [x] Auth: local / supabase / dual
- [x] Brain router + multi-provider LLM config
- [x] Workflow engine + `/api/workflows` CRUD / execute / runs
- [x] Scripts / PyRunner routes (`/api/scripts`)
- [x] Research routes (Hermes-oriented research module)
- [x] Ponytail cognitive pipeline routes
- [x] Workers, capabilities, evidence, governance, memory, terminal, files, VCS
- [x] MCP, marketplace, composer endpoints present

## Frontend

- [x] React app under `frontend-src/`; production assets under `frontend/`
- [x] Automation Hub: Graph + Matrix toggle
- [x] Layout: sidebar workspaces + right DevOS IDE dock
- [x] First-run onboarding wizard (`localStorage` key `devos_onboarded`)
- [x] Settings, chat, terminal, workers panels

## Honest limits

- Live LLM quality depends on configured provider keys / Ollama; not proven offline in CI without keys.
- Graph canvas may show demo topology when no saved workflows exist; Matrix prefers live `/api/scripts`.
- Full visual parity with design mocks is iterative; core routes and install path are the source of truth.
- Historical plan docs under `plans/` and long `record.md` may mention older names (e.g. Odysseus) or stage checklists that lag this snapshot — prefer this file + README for “what runs today.”

## Operator checklist

1. `./install.sh`
2. Set provider keys in `.env` if not using local Ollama only
3. `python3 cli.py start`
4. Open http://localhost:8000 — complete or skip setup wizard
5. `python3 cli.py doctor` if something fails
