# Agentic Runtime Multi-Turn E2E Proof

## Proof architecture

```
Agent Task
  → Turn N: PLANNING (deterministic or real-LLM planner)
  → structured capability request
  → UCIP authorization
  → ExecutionOperation / ExecutionJob
  → worker / substrate executor
  → evidence / result
  → durable checkpoint
  → Turn N+1 …
  → validate_completion → COMPLETED
```

## Required CI suite

- `tests/test_agentic_runtime.py`
- `tests/test_agentic_multiturn_e2e.py`
- `tests/test_agentic_proof_matrix.py`
- `tests/test_agentic_automation.py`
- `tests/test_agentic_e2e_gaps.py` (subset without live Postgres)

## PostgreSQL suite

- Concurrent duplicate agent capability → one operation (`test_pg_concurrent_*` when postgres URL set)

## Conditional real-LLM suite

- `tests/test_agentic_llm_planner.py`
- `tests/test_agentic_llm_planner_e2e.py`

Requires explicit provider configuration. Must not be the only proof of runtime correctness.

## Decision

See `docs/architecture/agentic-automation-runtime.md` for state machine, completion contract, and recovery matrix.


## Proof tiers (do not conflate)

| Tier | Gate | Counts as |
|------|------|-----------|
| Deterministic runtime / planner security | always | Runtime correctness |
| FakeLLM multi-turn companion | always | Schema + wiring (not real provider) |
| Real-provider smoke | `DEVOS_LLM_SMOKE=1` | Provider returns structured plan |
| Real-provider multi-turn consequential | `DEVOS_LLM_CONSEQUENTIAL_E2E=1` | Live model through UCIP path |
| PostgreSQL concurrency / durable ops | Postgres URL + sqlalchemy | Ledger identity |
| Combined real-provider + PostgreSQL | both gates | Full infrastructure proof |

**Planner integration proven ≠ real provider behavior proven.**

Skipped infrastructure must be reported as SKIPPED, never as PASS.

See `tests/test_real_provider_postgres_e2e.py`.
