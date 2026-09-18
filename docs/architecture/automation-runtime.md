# Automation Runtime

**Decision:** **READY_FOR_AUTOMATION_ORCHESTRATION**

## Lifecycle

```
AutomationRunRecord (queued)
        ↓
ExecutionJob claimed (worker ownership)
        ↓
execute_automation_run(run_id)
        ↓
load immutable definition_snapshot (never live draft)
        ↓
run_from_snapshot (sequential A → B → C)
        ↓
per-step UCIP / capability / isolation
        ↓
ExecutionState persisted on job
        ↓
AutomationRunRecord terminal + step summary
```

## Step execution contract

| Role | Authority |
|------|-----------|
| Automation runtime | Orchestrates order; does not grant capability |
| UCIP / substrate | Authorization for consequential steps |
| `run_isolated` / SandboxedExecutor | SCRIPT isolation |
| ExecutionOperation / Job | Consequential identity + lease |
| Evidence | Outcome proof |

## Step operation identity

```text
SHA-256({ run_id, workflow_version, step_id, occurrence })
```

Helper: `step_operation_idempotency_key(...)`.

Uses the global operation idempotency contract when steps reserve operations.

## Restart semantics

| Situation | Behavior |
|-----------|----------|
| Before step | Resume from `execution_state`; run pending step once |
| After success | Step in `completed`; not re-executed |
| RUNNING + side_effect | → STEP_UNKNOWN; run stops; no redispatch |
| RUNNING + no side_effect | Safe retry same step |
| Terminal SUCCEEDED run | Idempotent return |

## Failure / UNKNOWN

- Failed step stops the chain; later steps do not run.
- UNKNOWN is **not** FAILED for auto-retry; run is terminal failed with `pending_review` / permanent flag for the job path.
- No hidden automatic retries beyond existing step `retry` counts in the executor.

## Evidence

Consequential capability/script paths use existing evidence writers.  
Success is not inferred from recovery metadata.

## Supported step types

| Type | Path |
|------|------|
| SCRIPT | Isolation (`_run_script_step`) |
| HTTP | Credential-reference policy preserved |
| LOOP | Bounded |
| TRANSFORM | Pure / non-consequential |
| DATABASE | Governed, no raw SQL bypass |
| CAPABILITY | UCIP gate |

## Trigger integration

`trigger_automation_run` → enqueue `job_type=workflow` → `handle_workflow_job` → `execute_automation_run`.

## Concurrency

Job claim + worker ownership are authoritative. One worker owns the workflow job; run-level parallel workers are not introduced.

## Authoritative vs projected

| State | Authority |
|-------|-----------|
| Step consequential outcome | ExecutionOperation when reserved |
| Job lease | ExecutionJob |
| Step progress | Job payload execution_state + run result_summary |
| Automation listing | AutomationRunRecord |

## Final decision

**READY_FOR_AUTOMATION_ORCHESTRATION**
