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
