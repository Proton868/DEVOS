# Automation Orchestration

**Decision:** **READY_FOR_PARALLEL_AUTOMATION**

## Purpose

Control-flow layer for durable automations: **which step is eligible next**.

Step execution remains the automation runtime / workflow_executor responsibility.

```
Immutable workflow version
        ↓
select_eligible_steps (orchestrator)
        ↓
execute step (runtime only)
        ↓
UCIP / Operation / Job / Evidence
```

## Dependency model

Edges:

| Field | Meaning |
|-------|---------|
| `source` | completed step |
| `target` | candidate next step |
| `on` | `success` \| `failure` \| `true` \| `false` \| `always` |

Derived automatically from `WorkflowStep.next_step`, `on_error`, and `branches`.  
May also be declared in `definition.edges` / `metadata.edges`.

## Branch model

- **Success route:** `next_step` / edges `on=success`
- **Failure route:** `on_error` / edges `on=failure` (explicit only)
- **Condition routes:** `branches` true/false + CONDITION step outputs `result`

No failure edge → workflow stops after failure (downstream skipped).

## Condition language

Fail-closed (`_eval_condition`):

- literals: `true` / `false` / `1` / `0` / `yes` / `no`
- `path == value` / `path != value`
- bare path truthiness

Forbidden: `eval`, `exec`, imports, dunder paths.

## Join semantics

```json
{"target": "D", "deps": ["B", "C"], "mode": "all_success"}
```

Modes: `all_success`, `any_success`.

D is eligible only when the join predicate holds.

## Retry semantics

- Step-level `retry` remains in the executor (bounded attempts on safe retries).
- UNKNOWN is **never** auto-retried.
- New consequential attempts use `step_operation_idempotency_key(..., occurrence=N)`.

## UNKNOWN

Any `STEP_UNKNOWN` in run state → `select_eligible_steps` returns **[]** (pause).  
No success/failure branch advances until resolution outside automatic orchestration.

## Durable state

Execution progress lives in job `execution_state` and `AutomationRunRecord` (`_runtime_execution_state`).  
Branch decisions can be recorded under `context._branch_decisions`.

## Runtime boundary

Orchestrator **must not** call subprocess/HTTP/DB/script APIs.  
Only eligibility + invocation of `run_from_snapshot` / `execute_automation_run`.

## Flow integration

Flow UI may edit nodes/edges mirroring `next_step`, `on_error`, `branches`, `edges`, `joins`.  
Canonical authority remains the workflow definition JSON on `WorkflowRecord`.

## Final decision

**READY_FOR_PARALLEL_AUTOMATION**
