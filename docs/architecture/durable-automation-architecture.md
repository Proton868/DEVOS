# Durable Automation Architecture

**HEAD baseline:** automation durability layer on top of the proven execution spine.  
**Decision:** **READY_FOR_AUTOMATION_RUNTIME**

## Scope

Automation is the product surface for *recurring, triggered consequential work*.  
It **must not** become a second execution engine.

```
Automation Definition (WorkflowRecord)
        │
        ├── version (immutable snapshot at trigger)
        ├── trigger configuration
        └── enabled / disabled
                │
                ▼
         Trigger Resolution
                │
                ▼
        ExecutionOperation  ← authoritative consequential state
                │
                ▼
          ExecutionJob
                │
                ▼
       Existing execution spine (isolation, evidence, recovery)
                │
                ▼
   AutomationRunRecord (projection: trigger ↔ op/job ↔ version)
```

Flow UI remains an **editor/view** of `WorkflowRecord` / definition JSON — not an execution ledger.

## Canonical definition

| Field | Authority |
|-------|-----------|
| automation / workflow ID | `workflow_records.id` |
| owner / tenant | DB columns |
| name / description | DB columns |
| enabled | `workflow_records.enabled` |
| version (revision) | integer `workflow_records.version` |
| definition | JSON `definition` (graph/steps/triggers metadata) |
| status | draft / published / … |

Runtime `Workflow` objects are hydrated from DB (`brain.workflow_store`).

## Version model

- **Publish** (`publish_workflow_version`): marks definition executable (`status=published`).
- **Bump** (`bump_workflow_version`): increments revision for the next editable definition.
- **Trigger-time snapshot**: each run stores `definition_snapshot` + `workflow_version` so historical runs reconstruct the exact graph used.
- Editing after publish does **not** rewrite prior runs’ snapshots.

## Trigger model

| Type | Idempotency identity |
|------|----------------------|
| **manual** | Optional caller key; absent key → new operation each time |
| **webhook** | `delivery_id` (provider delivery/event id) |
| **event** | `delivery_id` |
| **schedule** | `schedule_occurrence` (deterministic slot timestamp/id) |

Key material (hashed): `workflow_id + version + trigger type + delivery/occurrence`.

Uses the existing **ExecutionOperation / ExecutionJob** idempotency contract — no second ledger.

Disabled automations raise `automation_disabled` and do not enqueue.  
Disable does **not** cancel already-queued/running jobs.

## Run model

`automation_run_records` is a **projection**:

- links automation version + trigger to `operation_id` / `job_id`
- survives process restart
- does **not** override operation UNKNOWN/SUCCEEDED semantics

In-process `AutomationRunStore` remains for unit tests / local cache only.

## Operation / job relationship

`trigger_automation_run`:

1. Checks `enabled`
2. Builds idempotency key from trigger delivery identity
3. Returns existing durable run if key already seen
4. Snapshots definition
5. `enqueue(job_type=workflow)` → atomic job + RESERVED operation
6. Persists run with op/job identities

## Restart / recovery

- Reload run via `load_run_durable`
- Consequential recovery remains **operation/job authoritative** (UNKNOWN, claim, ownership)
- Completed runs are not redispatched by trigger idempotency
- Immutable version remains on the run row

## Flow’s role

| Concern | Authority |
|---------|-----------|
| Graph editing | Flow UI → workflow_store → WorkflowRecord |
| Execution | ExecutionJob / Operation / isolation / evidence |
| Run list / status | AutomationRunRecord + job/op status |

## Authoritative vs derived

| State | Authoritative |
|-------|----------------|
| Definition | WorkflowRecord |
| Consequential outcome | ExecutionOperation (+ EvidenceRecord) |
| Work scheduling / lease | ExecutionJob |
| Trigger ↔ version ↔ op/job map | AutomationRunRecord (derived projection) |
| Process memory JSON store | Non-authoritative |

## Verification

Focused tests: `tests/test_durable_automation.py`  
Execution integrity remains as previously audited.

## Final decision

**READY_FOR_AUTOMATION_RUNTIME**
