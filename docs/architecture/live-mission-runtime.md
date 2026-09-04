# Live Mission Runtime

## Production vs test

| Mode | Env | Behavior |
|------|-----|----------|
| **Production** | default | Real `AgentRuntime` only |
| **Deterministic tests** | `DEVOS_ORCH_FAKE_RUNTIME=1` **and** (`PYTEST_CURRENT_TEST` or `DEVOS_ALLOW_FAKE_RUNTIME=1`) | Fake harness writes minimal artifacts |
| **Misconfiguration** | `DEVOS_ORCH_FAKE_RUNTIME=1` alone | `AGENT_RUNTIME_UNAVAILABLE` — **no silent success** |

Never fall back from real → fake in production.

## Boundary

```text
Mission Engine
  → NodeExecutionRequest
  → brain/orchestration_runtime.run_node_on_agent_runtime
  → AgentRuntime.run (events)
  → NodeExecutionResult + evidence
  → verification / NuhaDecision
```

## Required services

- Database (orchestration plan durability)
- Workspace / FileService
- Agent Runtime (brain)
- LLM provider configured via existing Settings/Persona
- UCIP governance module
- Jobs cancel path for mission cancel

## Useful env

- `DEVOS_ORCH_MAX_PARALLEL` (default 3)
- `DEVOS_ORCH_FAKE_RUNTIME` + `DEVOS_ALLOW_FAKE_RUNTIME` (tests only)
- Provider API keys via existing secrets/settings (never in repo)

## Health

`GET /api/health` includes `mission_runtime` probes:

- agent_runtime import
- fake_runtime_env flag
- orchestration_store
- ucip
- workspace

## How to run a live mission

1. Configure a provider in Settings (or env secrets already supported).
2. Ensure workspace is available for the user.
3. UI: MissionBar → Action mode → Nuha goal, **or**
   `POST /api/orchestration/plan` then `POST /api/orchestration/run`.
4. Observe MissionGlow / Agency strip + plan events.
5. Deployment steps must hit `waiting_for_user` / HITL — do not auto-deploy.

## How to run deterministic tests

```bash
DEVOS_ORCH_FAKE_RUNTIME=1 DEVOS_ALLOW_FAKE_RUNTIME=1 \
  PYTHONPATH=. pytest tests/test_mission_engine.py tests/test_evidence_control_loop.py \
  tests/test_orchestration_runtime_boundary.py -q
```

## Known limitations

- Full shoe-store LIVE E2E requires a working LLM + Agent Runtime credentials.
- Without them, missions fail honestly with `AGENT_RUNTIME_UNAVAILABLE` / `MODEL_UNAVAILABLE`.
- Parallel dispatch is bounded; write-path conflicts serialize.

## Bring-up report (2026-09-04 sandbox)

### Path traced

```text
API/UI → orchestration → mission_engine → specialty ∩ UCIP
  → orchestration_runtime → AgentRuntime.run → BrainLLM → workspace
```

### Issues found and fixed in code

1. **SYSTEM_PROMPT.format KeyError `"thought"`**  
   JSON example braces in `brain/agent_runtime.py` were not escaped.  
   Fixed by doubling braces so only `{tools}` is substituted.

### Live smoke results (this environment)

| Step | Result |
|------|--------|
| Reach AgentRuntime | **PASS** — task IDs issued, `agent.started` events |
| Format system prompt | **PASS** after fix |
| Load BrainLLM / settings | **BLOCKED** — `pydantic_settings` not installed in bare sandbox Python; pip mirror 502 |
| LLM provider | **BLOCKED** — no `OPENROUTER_API_KEY` / cloud keys in `.env`; Ollama `127.0.0.1:11434` not responding; remote ollama host error 1033 |
| Workspace artifact | **BLOCKED** — depends on LLM tool loop |
| Full shoe-store LIVE E2E | **BLOCKED** — provider + full app deps |

Honest production path after prompt fix:

```text
SUCCESS False
STATUS error
ERROR AGENT_RUNTIME_UNAVAILABLE: No module named 'pydantic_settings'
```

(or `MODEL_UNAVAILABLE` once deps exist but keys do not)

### Operator checklist to go green

1. Install project deps (`pip install -r requirements.txt` or Docker compose).
2. Set one of: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, or reachable Ollama.
3. `DEFAULT_PROVIDER` matching that provider.
4. Start app; `GET /api/health` → `mission_runtime.agent_runtime: import_ok`, providers non-empty.
5. Minimal mission: create `hello.txt` via Plan/Action; inspect workspace file.
6. Then shoe-store acceptance.

### Fake runtime

Still **test-only**. Never enable `DEVOS_ORCH_FAKE_RUNTIME` in production.
