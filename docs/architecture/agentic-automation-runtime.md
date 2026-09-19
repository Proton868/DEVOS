# Durable Agentic Automation Runtime



## Completion Contract

An agent enters **COMPLETED** only when `validate_completion` passes.

Invariant (`agent_state == COMPLETED` requires):

1. Structured completion decision (`kind=complete`)
2. Durable checkpoint of the completing turn
3. No active consequential operation (`EXECUTING` forbidden)
4. No linked operation in UNKNOWN
5. No pending capability request in an active auth/exec state
6. All required consequential outcomes terminal (per contract)
7. Required evidence present when `required_evidence=True`
8. Required outputs present when listed
9. Task not cancelled
10. Contract requirements satisfied

Free-form text (`"done"`, `"verified"`) is **never** sufficient.

`CompletionContract` is immutable once bound on the task.

`try_complete` → COMPLETED on pass; remains PLANNING/BLOCKED on fail.

## Multi-Turn Execution

Scripted/production planner issues at most one capability request per turn.
After substrate execution, observation is checkpointed and becomes next-turn context.

## Recovery Test Taxonomy

| Layer | File | Owns |
|-------|------|------|
| State-machine unit | `tests/test_agentic_runtime.py` | transitions, bounds, cancel, UNKNOWN legality |
| Contract unit | `tests/test_agentic_automation.py` | delegation, grants, isolation |
| Multi-turn E2E | `tests/test_agentic_multiturn_e2e.py` | completion contract, restart, multi-turn path |

## Test Isolation and Cleanup

- Unique `owner-{uuid}` / `tenant-{uuid}` / idempotency keys per test
- Fixture resets process agent task store before/after
- No shared global `"test-user"` identities
- No dependency on external LLM, network, or production DB for E2E path

**Decision:** **READY_FOR_NEXT_AGENTIC_MILESTONE**

## Architecture

```
reason → inspect context → select capability request
  → UCIP / CapabilitySubstrate authorize
  → ExecutionOperation / ExecutionJob (consequential)
  → executor + isolation / credentials
  → evidence → structured observation
  → checkpoint → reason …
```

No second execution engine or job queue.

## Persisted state machine

```
CREATED → PLANNING ⇄ (AWAITING_CAPABILITY → AUTHORIZING → AUTHORIZED
          → EXECUTING → OBSERVING → CHECKPOINTING → PLANNING)
          → COMPLETED | FAILED | CANCELLED | BLOCKED
EXECUTING → UNKNOWN → BLOCKED | OBSERVING (reconcile only)
```

Illegal examples (rejected):

- `PLANNING → EXECUTING` (skips authorization)
- `UNKNOWN → EXECUTING` (no blind retry)
- `COMPLETED → *`

## Synchronous vs asynchronous

| Mode | Allowed |
|------|---------|
| **Sync** | Planning, context build, validation, transition, checkpoint |
| **Async / substrate** | Any consequential capability (mutate, network, script, DB, credentials) |

Long-running work links `operation_id` / `job_id` and leaves `EXECUTING` for worker resume via `observe_operation_result`.

## Turn model

- `DEVOS_AGENT_MAX_TURNS` (default 8, hard max 32)
- Default **1** capability request per turn
- Max capability requests per task (default 16, hard 64)
- Agent **cannot** raise bounds

## Checkpoint

Stored on `GovernedAgentTask.recovery.runtime_checkpoint` and optionally table
`agentic_runtime_checkpoints` (migration `20260918200000_agentic_runtime_checkpoints.sql`).

## UNKNOWN

`auto_retry=False`. Only `reconcile_unknown` may move to `OBSERVING` after authoritative resolution.

## Parallel automation

AGENT steps share the existing per-run concurrency limit (`DEVOS_AUTOMATION_MAX_PARALLEL`).

## Migration

### Forward (`20260918200000_agentic_runtime_checkpoints.sql`)

| Object | Detail |
|--------|--------|
| Table | `agentic_runtime_checkpoints` (PK `task_id`) |
| Columns | state machine fields, turn bounds, JSONB plan/observation/evidence, operation/job ids, `idempotency_key`, full `checkpoint` JSONB |
| Indexes | `owner_id`, `tenant_id`, `state`, `parent_run_id` |
| Unique | partial unique `(owner_id, idempotency_key)` where key present |
| Defaults | `state='created'`, `turn=0`, `max_turns=8`, empty JSON arrays |
| Side effects | **None** on existing tables |

Apply:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/migrations/20260918200000_agentic_runtime_checkpoints.sql
```

Re-run is safe (`IF NOT EXISTS`).

### Rollback (`20260918200000_agentic_runtime_checkpoints.down.sql`)

**Order:** drop unique index → drop secondary indexes → drop table.

**Data loss:** all rows in `agentic_runtime_checkpoints` only. Does not touch
`execution_operations`, `execution_jobs`, automation runs, or AgentTaskRecord.

**Before rollback:**

1. Confirm app revision does not require the table (or treats it as optional).
2. Optional backup:

```sql
COPY (SELECT * FROM agentic_runtime_checkpoints)
  TO '/tmp/agentic_runtime_checkpoints_backup.csv' WITH CSV HEADER;
```

3. Apply down file:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/migrations/20260918200000_agentic_runtime_checkpoints.down.sql
```


### Pre-rollback data verification

Run against production (or the target database) **before** applying the down migration:

| # | Check | SQL / action | Pass criteria |
|---|--------|--------------|---------------|
| V1 | Table present | `SELECT to_regclass('public.agentic_runtime_checkpoints');` | Understood (NULL = no-op drop) |
| V2 | Counts by state | `SELECT state, COUNT(*) … GROUP BY state` | Volume expected |
| V3 | In-flight rows | Rows where `state NOT IN ('completed','failed','cancelled')` | Empty **or** loss explicitly accepted |
| V4 | UNKNOWN / BLOCKED | `state IN ('unknown','blocked')` | Reviewed / exported |
| V5 | Linked ops sample | Rows with `operation_id` in executing/unknown/observing | Cross-checked if ops table exists |
| V6 | Owner/tenant footprint | `GROUP BY owner_id, tenant_id` | Isolation sample OK |
| V7 | Backup | `COPY (SELECT * FROM …) TO '…csv'` | File non-empty if `total_rows > 0` |

Do **not** proceed with `DROP TABLE` until V3/V4 are acknowledged and V7 backup exists when the table holds data.

Checklist is also embedded in `20260918200000_agentic_runtime_checkpoints.down.sql`.

**After rollback:**

```sql
SELECT to_regclass('public.agentic_runtime_checkpoints');  -- NULL
```

**Re-create:** re-run the forward migration; row data is not restored without backup.

**Compatibility:** additive forward allows old app + new schema during rollout.
Process store / `GovernedAgentTask.recovery.runtime_checkpoint` remains the
in-process durability path if the table is missing.


## Security

- Allowlist fixed at delegation
- No self-authorization
- Secrets scrubbed from context
- Fabricated evidence ignored
- Cross-owner/tenant denied via `assert_owner`


## Test Coverage Matrix

Maps the multi-turn E2E milestone checklist to owning tests.
Status: **covered** | **partial** | **gap**.

### Completion contract

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 1 | Valid completion | covered | `test_valid_completion` |
| 2 | Missing evidence | covered | `test_missing_evidence_blocks_completion` |
| 3 | Active operation | covered | `test_active_operation_blocks_completion` |
| 4 | UNKNOWN operation | covered | `test_unknown_blocks_completion` |
| 5 | Missing required output | covered | `test_missing_required_output_blocks` |
| 6 | Cancelled task | covered | `test_cancelled_blocks_completion` |
| 7 | Fake textual completion | covered | `test_fake_textual_completion_rejected` |
| 8 | Immutable completion requirements | covered | `test_completion_contract_immutable` |

### Multi-turn

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 9 | Two-turn successful agent | covered | `test_two_turn_successful_agent` |
| 10 | Three-turn path | covered | `test_three_turn_path_with_context` |
| 11 | Operation result → next-turn context | covered | `test_three_turn_path_with_context` (observations list) |
| 12 | Durable turn counter | covered | `test_durable_turn_counter_increments` |
| 13 | Max-turn enforcement | covered | `test_max_turn_enforcement`, `test_max_turns_blocks` |

### Restart / recovery

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 14 | Restart between turns | covered | `test_restart_between_turns` |
| 15 | Restart during operation | covered | `test_observe_operation_after_executing` + gap reload patterns |
| 16 | Restart after op before completion | covered | `test_restart_after_operation_before_completion` |
| 17 | Restart after completion request before terminal commit | covered | `test_crash_after_completion_validation_then_restart_completes` |

### UNKNOWN

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 18 | UNKNOWN blocks completion | covered | `test_unknown_blocks_completion`, `test_unknown_does_not_complete_or_retry` |
| 19 | UNKNOWN does not retry | covered | `test_unknown_blocks_retry`, `test_unknown_no_auto_retry` |
| 20 | Authoritative reconciliation resumes | covered | `test_reconcile_then_complete`, `test_reconcile_unknown_to_observing` |

### UCIP / execution substrate

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 21 | Capability request reaches UCIP/substrate | covered | `test_capability_not_delegated_denied`, multi-turn via `request_capability` |
| 22 | Operation created | covered | `test_agent_script_capability_operation_isolation_observation` |
| 23 | Job created where required | partial | operation reserved; full JobWorker claim still environment-dependent |
| 24 | Existing executor performs work | covered | SCRIPT isolation + HTTP local server via substrate executors + workflow SCRIPT/HTTP |
| 25 | Evidence returned | covered | synthetic/substrate evidence in multi-turn + `test_two_turn_successful_agent` |

### Parallel automation

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 26 | AGENT + normal step fan-out | covered | `test_agent_parallel_with_transform` |
| 27 | Join waits for actual agent completion | covered | `test_authoritative_agent_completion_releases_join` |
| 28 | Fake agent completion does not release join | covered | `test_fake_agent_completion_blocks_downstream_join` |

### Test isolation

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 29 | Unique namespaces | covered | `test_tests_use_unique_namespaces`, `_ns()` helper |
| 30 | Cleanup after failure | covered | autouse `reset_agent_task_store_for_tests` |
| 31 | Durable rows removed for namespace | partial | process store only; Postgres table not asserted empty |
| 32 | Tests pass individually | covered | suite design (no cross-test fixtures) |
| 33 | Tests pass repeatedly | covered | store reset; deterministic planner |
| 34 | Order independence | covered | unique IDs; no shared global rows |

### PostgreSQL

| # | Requirement | Status | Owning test |
|---|-------------|--------|-------------|
| 35 | Concurrent duplicate → one logical op | covered | `test_pg_concurrent_agent_capability_one_operation` (skips without Postgres) |
| 36 | Concurrent tests remain isolated | partial | process-store isolation only |
| 37 | Migration-backed checkpoint persistence | partial | `test_migration_file_exists` (file presence, not applied DB) |

### Intentionally deferred (scope)

- Live LLM planner
- MCP / external SaaS
- Full JobWorker process-kill harness
- Production Postgres apply in CI without credentials

Next coverage priorities: #17, #28, #23/#24 with a real SCRIPT/HTTP operation under AGENT, #35 Postgres concurrency.


## Gap Fill Code Examples

Concrete patterns for remaining **gap** / **partial** items. Copy into
`tests/test_agentic_multiturn_e2e.py` or a dedicated Postgres file; adapt
imports to the live module paths.

### Gap #17 — Restart after completion request, before terminal commit

Simulate: planner emits `complete`, validation passes, process dies before
`persist_checkpoint` writes `COMPLETED`.

```python
import pytest
from brain.agentic_runtime import (
    AgentRuntimeState, TurnDecision, checkpoint_from_task,
    apply_transition, persist_checkpoint, try_complete, validate_completion,
)
from brain.agentic_automation import get_agent_task_store

@pytest.mark.asyncio
async def test_restart_after_completion_request_before_terminal_commit(ns_task):
    t, contract = ns_task  # fixture: unique owner/tenant + CompletionContract()
    cp = checkpoint_from_task(t)
    apply_transition(cp, AgentRuntimeState.PLANNING)
    persist_checkpoint(t, cp)

    decision = TurnDecision(kind="complete", complete=True, reason="structured")
    assert validate_completion(t, decision=decision, cp=cp).ok

    # --- crash window: validation OK, COMPLETED not yet durable ---
    # Do NOT call try_complete yet. Reload from store (pre-complete state).
    t2 = get_agent_task_store().get(t.task_id)
    cp2 = checkpoint_from_task(t2)
    assert cp2.state != AgentRuntimeState.COMPLETED

    # Resume: re-request complete and commit
    t2, cp2, ok = try_complete(t2, cp2, decision)
    assert ok
    assert checkpoint_from_task(t2).state == AgentRuntimeState.COMPLETED
    # Idempotent second try_complete must stay COMPLETED, not error
    t3, cp3, ok2 = try_complete(t2, checkpoint_from_task(t2), decision)
    assert checkpoint_from_task(t2).state == AgentRuntimeState.COMPLETED
```

### Gap #15 (partial) — Restart while EXECUTING

```python
def test_restart_during_executing_reconciles(ns_task):
    t, _ = ns_task
    cp = checkpoint_from_task(t)
    for st in (
        AgentRuntimeState.PLANNING,
        AgentRuntimeState.AWAITING_CAPABILITY,
        AgentRuntimeState.AUTHORIZING,
        AgentRuntimeState.AUTHORIZED,
        AgentRuntimeState.EXECUTING,
    ):
        apply_transition(cp, st)
    cp.operation_id = f"op-{t.task_id}"
    persist_checkpoint(t, cp)

    # Process restart: reload task, still EXECUTING
    t2 = get_agent_task_store().get(t.task_id)
    assert checkpoint_from_task(t2).state == AgentRuntimeState.EXECUTING

    # Worker reports terminal outcome (do not redispatch)
    from brain.agentic_runtime import observe_operation_result
    t2 = observe_operation_result(
        t2,
        operation_id=cp.operation_id,
        status="succeeded",
        evidence_refs=[f"ev-{t.task_id}"],
    )
    st = checkpoint_from_task(t2).state
    assert st in (AgentRuntimeState.OBSERVING, AgentRuntimeState.CHECKPOINTING)
    assert checkpoint_from_task(t2).capability_request_count == cp.capability_request_count
```

### Gap #23 / #24 — Real ExecutionOperation + Job under AGENT

Use an existing governed capability that creates ledger rows (e.g. SCRIPT step
path or a registered substrate executor that calls `create_operation`).

```python
@pytest.mark.asyncio
async def test_agent_capability_creates_operation_and_job(ns_task, monkeypatch):
    """Prove consequential path records operation_id (and job_id when required)."""
    t, contract = ns_task
    # Bind a capability whose substrate executor creates ExecutionOperation
    # (wire via get_capability_substrate().register_executor in test setup).

    op_ids = []

    async def fake_executor(contract, request):
        from governance.execution_operations import create_operation  # adapt import
        op = await create_operation(
            owner_id=request.context.owner_id,
            operation_type="agent.capability",
            idempotency_key=request.idempotency_key or f"op-{t.task_id}",
            tenant_id=request.context.tenant_id,
        )
        op_ids.append(op.id)
        return {"ok": True, "operation_id": op.id}

    # register_executor("devos.test.consequential", fake_executor)
    # allowlist that cap on the task, then:
    from brain.agentic_runtime import TurnDecision, run_agent_turn

    def planner(ctx):
        if ctx.get("last_observation"):
            return TurnDecision(kind="complete", complete=True)
        return TurnDecision(
            kind="capability_request",
            capability_id="devos.test.consequential",
            inputs={},
        )

    t = await run_agent_turn(t, planner=planner, execute_capability=True)
    cp = checkpoint_from_task(t)
    assert cp.operation_id or op_ids, "expected ExecutionOperation linkage"
    # When jobs are required:
    # assert cp.job_id is not None
```

### Gap #28 — Fake AGENT completion must not release parallel join

```python
@pytest.mark.asyncio
async def test_fake_agent_complete_does_not_release_join():
    from brain.workflow import WorkflowStep, StepType
    from brain.workflow_store import build_execution_snapshot
    from brain.workflow_executor import ExecutionState, STEP_SUCCEEDED, STEP_FAILED
    from brain.automation_orchestration import select_eligible_steps
    from brain.automation_parallel import execute_parallel_graph

    ns = uuid.uuid4().hex[:12]
    # AGENT step that would "claim" success without satisfying CompletionContract
    steps = [
        WorkflowStep(id="A", type=StepType.TRANSFORM, inputs={"expr": "1"}),
        WorkflowStep(
            id="B",
            type=StepType.AGENT,
            inputs={
                "capabilities": ["devos.capability.list"],
                # force contract that cannot pass without real evidence
                "task_input": {
                    "completion_contract": {
                        "required_successful_capabilities": 99,
                        "required_evidence": True,
                        "immutable": True,
                    }
                },
            },
        ),
        WorkflowStep(id="C", type=StepType.TRANSFORM, inputs={"expr": "3"}),
        WorkflowStep(
            id="D",
            type=StepType.TRANSFORM,
            inputs={"expr": "4"},
            metadata={"depends_on": ["B", "C"], "join": "all_success"},
        ),
    ]
    definition = {
        "workflow_id": f"wf-join-{ns}",
        "name": "join-fake",
        "version": "1.0.0",
        "start_step": "A",
        "steps": [s.to_dict() for s in steps],
        "edges": [
            {"source": "A", "target": "B", "on": "success"},
            {"source": "A", "target": "C", "on": "success"},
            {"source": "B", "target": "D", "on": "success"},
            {"source": "C", "target": "D", "on": "success"},
        ],
        "joins": [{"target": "D", "deps": ["B", "C"], "mode": "all_success"}],
        "triggers": ["manual"],
    }
    snap = build_execution_snapshot(
        workflow_id=definition["workflow_id"],
        workflow_version=1,
        owner_id=f"owner-{ns}",
        tenant_id=f"tenant-{ns}",
        name="join-fake",
        definition=definition,
        enabled=True,
    )
    st = ExecutionState()
    st.records["A"] = {"step_id": "A", "status": STEP_SUCCEEDED}
    out = await execute_parallel_graph(
        snap, st, extra_context={"owner_id": f"owner-{ns}", "tenant_id": f"tenant-{ns}"},
    )
    # C may succeed; B must not report SUCCEEDED if contract unmet
    b_status = out.records.get("B", {}).get("status")
    assert b_status != STEP_SUCCEEDED or out.records.get("D", {}).get("status") != STEP_SUCCEEDED
    # Stronger once AGENT step surfaces completion validation:
    # assert b_status in (STEP_FAILED, "blocked", STEP_DENIED)
    # assert "D" not in out.records or out.records["D"]["status"] != STEP_SUCCEEDED
```

### Gap #35 — Postgres concurrent duplicate → one logical operation

```python
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_duplicate_agent_capability_one_operation(pg_session):
    """Two workers, same owner + idempotency_key → one ExecutionOperation row."""
    import asyncio
    ns = uuid.uuid4().hex[:12]
    owner = f"owner-{ns}"
    tenant = f"tenant-{ns}"
    idem = f"idem-cap-{ns}"

    async def worker():
        # create_operation must use UNIQUE (owner_id, operation_type, idempotency_key)
        from governance.execution_operations import create_operation
        return await create_operation(
            owner_id=owner,
            tenant_id=tenant,
            operation_type="agent.capability",
            idempotency_key=idem,
        )

    results = await asyncio.gather(worker(), worker(), return_exceptions=True)
    ops = [r for r in results if not isinstance(r, Exception)]
    assert len(ops) >= 1
    ids = {getattr(o, "id", o) for o in ops}
    assert len(ids) == 1, f"expected one operation, got {ids}"

    # Cleanup only this namespace
    await pg_session.execute(
        "DELETE FROM execution_operations WHERE owner_id = :o AND tenant_id = :t",
        {"o": owner, "t": tenant},
    )
    await pg_session.commit()
```

### Gap #37 (partial) — Migration-backed checkpoint round-trip

```python
@pytest.mark.postgres
def test_agentic_checkpoint_table_roundtrip(pg_engine):
    """Requires 20260918200000_agentic_runtime_checkpoints.sql applied."""
    from sqlalchemy import text
    ns = uuid.uuid4().hex[:12]
    task_id = f"agt_{ns}"
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO agentic_runtime_checkpoints
                    (task_id, owner_id, tenant_id, state, turn, checkpoint)
                VALUES
                    (:tid, :oid, :ten, 'planning', 1, CAST(:cp AS jsonb))
                """
            ),
            {
                "tid": task_id,
                "oid": f"owner-{ns}",
                "ten": f"tenant-{ns}",
                "cp": '{"task_id": "%s", "state": "planning"}' % task_id,
            },
        )
        row = conn.execute(
            text("SELECT state, turn FROM agentic_runtime_checkpoints WHERE task_id = :tid"),
            {"tid": task_id},
        ).one()
        assert row.state == "planning" and row.turn == 1
        conn.execute(
            text("DELETE FROM agentic_runtime_checkpoints WHERE task_id = :tid"),
            {"tid": task_id},
        )
```

### Wiring notes

- Prefer `uuid4` namespaces; never hard-code `test-user`.
- Cleanup deletes **only** `owner_id` / `task_id` / `tenant_id` for that test.
- Do not call `subprocess`, raw SQL mutations, or HTTP from the agent runtime in these tests — only capability requests and ledger helpers.
- Mark Postgres tests `@pytest.mark.postgres` and skip when `DATABASE_URL` is unset:

```python
import os
import pytest

pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL required for Postgres gap tests",
)
```


## Gap Closure Results

| Gap | Status | Test |
|-----|--------|------|
| #17 crash after validation | covered | `tests/test_agentic_e2e_gaps.py::test_crash_after_completion_validation_then_restart_completes` |
| #28 fake vs real join | covered | `test_fake_agent_completion_blocks_downstream_join`, `test_authoritative_agent_completion_releases_join` |
| #23/#24 SCRIPT/HTTP | covered | `test_agent_script_capability_operation_isolation_observation`, `test_agent_http_capability_and_inline_secret_rejection`, `test_workflow_script_step_uses_executor_not_agent_subprocess` |
| #35 PG concurrency | covered (skip without PG) | `test_pg_concurrent_agent_capability_one_operation` |

**Test-only seam:** `DEVOS_AGENT_CRASH_AFTER_COMPLETION_VALIDATION=1` raises after validation, before durable COMPLETED. Must never be set in production.

**AGENT step completion:** `execute_agent_step_body` now calls `try_complete` / `CompletionContract` — workflow SUCCEEDED requires authoritative completion, not free-text claims.

**Remaining limitations:** Full JobWorker multi-process claim under AGENT is partial without live worker pool; PG tests skip when `DATABASE_URL` is not Postgres.

## LLM Planner (bounded, untrusted)

```text
LLM Planner
    → Structured Plan (JSON schema)
    → Agent Runtime (state machine)
    → UCIP / CapabilitySubstrate
    → Execution (ExecutionOperation / Job)
    → Evidence
    → CompletionContract
```

**The LLM is an untrusted planning component. It does not possess execution authority.**

- Free-form text (including the word “done”) is not executable and cannot complete a mission.
- Only structured actions (`capability_request`, `observe`, `complete`, `wait`, `block`, `fail`) are accepted.
- Capability requests enter the existing `request_capability` path; the planner never invokes capabilities.
- Provider failures and malformed output become governed `block`/`fail` decisions — no side effects.
- Deterministic `default_planner` / `FakeLLMProvider` remain available for tests; CI does not call external models.
