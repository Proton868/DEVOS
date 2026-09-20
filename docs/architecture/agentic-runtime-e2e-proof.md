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
