# Prime: controlled specialist mission verification

**Scope:** Operator-run on VPS `prime` only.  
Coding agents **without** VPS access must not claim these steps passed.

This procedure proves the real path:

```text
auth → Nuha chat → create_plan → run_delegated_mission
  → AgentRuntime → OmniRoute (DEFAULT_PROVIDER)
  → tools / workspace files
  → Ponytail → evidence → acceptance → SSE terminal
```

## Preconditions

```bash
cd ~/devos
git fetch origin && git checkout main && git reset --hard origin/main
git rev-parse HEAD   # record SHA

sudo systemctl status devos --no-pager
curl -fsS http://127.0.0.1:8000/api/health | tee /tmp/devos_health.json
# Require: status=ok, db_backend=postgres, default_provider=omniroute,
#          mission_runtime.fake_runtime_env=false (or equivalent)

curl -fsS http://127.0.0.1:3000/api/v1/models | head -c 200
# OmniRoute must respond

# Confirm fake runtime is OFF in the service environment
sudo systemctl show devos -p Environment -p EnvironmentFiles
# DEVOS_ORCH_FAKE_RUNTIME must not be 1 in production
```

## Authenticate

Use an existing Supabase user (do not print tokens in logs).

```bash
# Obtain DevOS JWT via /api/auth/supabase/exchange (same flow as production UI)
# export DEVOS_TOKEN=...   # session token only; never commit
```

## Controlled mission (harmless)

Send a minimal request that **requires** specialist work (so orchestration runs):

```text
Create a single file named prime_probe.txt in the workspace containing exactly: ok
```

Or via API (shape depends on deployed client; SSE from POST /api/chat/send):

Observe SSE `status` values in order where applicable:

1. `received` / `classifying`
2. `planning`
3. `plan_created`
4. `delegating`
5. `agent_progress` (phases: `mission_task_assigned`, `a2a_delegate_sent`, …)
6. `worker_completed` or further `agent_progress` (`specialist_execution`)
7. `validation_started` (`ponytail`)
8. `artifact_created` and/or `validation_completed` **or** `failed`

## Proof checklist

| Stage | How to prove |
|-------|----------------|
| Auth | HTTP 200 on `/api/auth/me` with token |
| Plan | `plan_id` in SSE |
| Delegation | `delegating` + progress phases |
| Real AgentRuntime | `specialist_execution` progress; **not** FAKE_RUNTIME |
| OmniRoute | Provider logs / OmniRoute access log around mission time; `default_provider=omniroute` |
| Artifact | Workspace file exists for that user/project |
| Evidence/acceptance | Mission accepted only if Ponytail+evidence path succeeds |
| Terminal | Final SSE `done` / failure status matches mission truth |

## Failure handling

If the first failure is provider/model:

- Record `MODEL_UNAVAILABLE` / OmniRoute errors — do **not** enable FAKE_RUNTIME.

If the first failure is authorization:

- Leave UCIP denials intact; fix capability mapping only if incorrect.

## Result recording

Append outcomes to `docs/CURRENT_STATUS.md` under **LIVE PRIME VERIFIED** only when this procedure was actually run.
