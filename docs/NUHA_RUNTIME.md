# Nuha Runtime — Canonical Execution Spine

> Status labels: **IMPLEMENTED** · **TESTED** · **LIVE-VERIFIED** · **UNPROVEN**

This document is the source of truth for how Nuha work is executed in DevOS.
It supersedes informal descriptions in older IDE/cognitive docs where they conflict.

## Canonical path

```
User
  ↓
Nuha (chat / intent)
  ↓
Nuha Bridge (brain/nuha_bridge.py)
  ↓
Mission / Orchestration Plan (create_plan)
  ↓
Mission DAG + Mission Engine
  ↓
Specialty Policy ∩ UCIP
  ↓
AgentRuntime (run_node_on_agent_runtime)
  ↓
Tools / Workspace
  ↓
Artifacts + Evidence
  ↓
Machine Verification (orchestration_verify)
  ↓
Mission Truth (mission_truth)
  ↓
Durable plan/task state
  ↓
SSE → Nuha / UI
```

There is **one authoritative execution path** for delegated work:

**MISSION_EXECUTION**

Nuha is the front door. Mission Engine orchestrates. UCIP authorizes.
AgentRuntime executes. Verification decides truth. Mission Truth is final status.
SSE communicates state. The UI does not invent state.

XP / learning is **never** an authority layer.

## Execution path labels

| Label | Meaning |
|-------|---------|
| `MISSION_EXECUTION` | Plan → DAG → UCIP → AgentRuntime → verify → mission_truth |
| `DIRECT_SCAFFOLD` | Lightweight workspace scaffold (e.g. website files). **Not** specialist mission success. |

These are **not** interchangeable.

- `scaffold_created` ≠ `mission_verified`
- File write alone ≠ `SUCCEEDED`
- LLM saying “Done” ≠ mission success

## Terminal semantics

Mission/plan status is interpreted by `mission_truth()`:

| Status class | `ok` | synthesis_mode |
|--------------|------|----------------|
| completed / succeeded / verified | true | success |
| failed / cancelled / blocked / verification_failed | false | failure |
| waiting_for_user / awaiting_approval | false | waiting |
| running / unknown / intermediate | false | incomplete |

If the runtime cannot establish truth, prefer **failure/incomplete/UNKNOWN** over inventing success.

## Verification

After AgentRuntime returns success for a node, Mission Engine **must** run
`verify_workspace_artifacts` before the node can become COMPLETED.

Hard rules:

- `execution_success` alone is not enough when verification is required.
- Agent-reported `files_changed` without on-disk confirmation is **weak**, not `passed`.
- Website/page/landing goals require entry artifacts on disk (html/jsx/tsx or package.json).
- Verification failure → node FAILED (`VERIFICATION_FAILURE`) → mission aggregation fails.

Evidence includes: `passed`, `verified`, `checks`, `failed_criteria`, `verifier`.

## Mission Truth → SSE → synthesis

```
plan.status
  → mission_truth()
  → orch_result.ok / synthesis_mode / execution_path
  → SSE done.status (completed | failed | waiting_for_user)
  → synthesize_orchestration_reply()  # presenter only
```

Synthesis **must not** claim success when `ok` is false.

## What is proven vs unproven

| Capability | Status |
|------------|--------|
| Mission path + UCIP + AgentRuntime | IMPLEMENTED |
| mission_truth + SSE terminal mapping | IMPLEMENTED + TESTED |
| Verification gate on node COMPLETED | IMPLEMENTED + TESTED (unit) |
| DIRECT_SCAFFOLD vs MISSION_EXECUTION labels | IMPLEMENTED |
| Authenticated SSE ≤500ms / no 504 | **UNPROVEN** (live) |
| Cancel → process tree termination | **UNPROVEN** (live) |
| HITL durable across restart | **UNPROVEN** (live) |
| Fully detached async execution (return id, poll/stream) | PARTIAL (SSE still hosts long work) |
| Skills / A2A / full compaction | Not required for this spine |

## Related modules

| Module | Role |
|--------|------|
| `api/routes/chat.py` | Nuha SSE entry |
| `brain/nuha_bridge.py` | classify, mission_truth, synthesis |
| `brain/orchestration.py` | plan lifecycle |
| `brain/mission_engine.py` | DAG dispatch, verify, aggregate |
| `brain/orchestration_runtime.py` | AgentRuntime boundary |
| `brain/orchestration_verify.py` | machine verification |
| `governance/ucip.py` | authorization |
| `brain/agent_runtime.py` | tool loop |

## Explicit non-goals (for this document)

- Installing A2A as the internal kernel
- Replacing Mission Engine or UCIP
- Claiming production readiness without live gates on the real host

## Async execution handoff (IMPLEMENTED)

`POST /api/orchestration/run` with `"background": true` (or `?background=1`):

1. Creates/loads plan
2. Persists plan
3. Emits `execution.created` / `execution.started`
4. Returns `{ execution_id, plan_id, stream }` immediately
5. Runs `execute_plan` in a background task

Observe progress:

`GET /api/orchestration/{plan_id}/events?after=N`

Events include monotonic `sequence`, `event_id`, `type`, `payload`.

Chat SSE (`POST /api/chat/send`) still streams progressive status for conversational missions; long work should prefer orchestration `background` + event polling/stream to avoid proxy 504s.

| Async path | Status |
|------------|--------|
| Sequenced plan events | IMPLEMENTED + TESTED |
| Event replay `?after=N` | IMPLEMENTED |
| Background `/run` | IMPLEMENTED |
| Chat fully detached from mission lifetime | PARTIAL |
| Live no-504 on production proxies | UNPROVEN |

## Chat SSE event drain (IMPLEMENTED)

During auto-orchestrated chat, after `plan_created` Nuha chat:

1. Persists the plan
2. Starts `execute_plan` as a shielded background task
3. Streams `status=event` frames with `sequence` / `type` / `payload` as plan events grow
4. Emits `executing` heartbeats every ~2s
5. Terminal `done` uses `mission_truth(plan.status)` — not LLM narrative

Clients may also poll `GET /api/orchestration/{plan_id}/events?after=N` if the SSE connection drops.
