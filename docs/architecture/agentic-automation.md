# Governed Agentic Automation

**Decision:** **READY_FOR_AGENTIC_AUTOMATION_RUNTIME**

## Principle

```
Nuha proposes/orchestrates
        ↓
delegate_agent_task (bounded capabilities)
        ↓
UCIP / CapabilitySubstrate
        ↓
ExecutionOperation / ExecutionJob (consequential only)
        ↓
Existing runtime executor + isolation / credentials
        ↓
Evidence (substrate only)
        ↓
Agent/Nuha receives structured result
```

Agents are **never** an unrestricted execution backdoor.

## Agent / task boundary

| Record | Authority |
|--------|-----------|
| `GovernedAgentTask` | Agent work/state, plan, delegated caps |
| `ExecutionOperation` | Authoritative consequential identity |
| `ExecutionJob` | Durable claim/worker unit |
| Agent free-text ("done", "verified") | **Not evidence** |

## Nuha delegation

`delegate_agent_task(...)`:

1. Validates owner
2. Rejects `client_supplied_grants`
3. Resolves capabilities (forbids `execute_anything` / unrestricted)
4. Creates durable task + idempotency key
5. Does **not** execute side effects by itself

## Capability requests

`request_capability(task, capability_id, execute=...)`:

- Capability must be in `allowed_capabilities`
- Invokes **CapabilitySubstrate** with `InvocationContext.granted_capabilities` = delegated set
- Denial → no job, no fabricated evidence

## Workflow AGENT step

`StepType.AGENT` → `_run_agent_step` → `execute_agent_step_body`

Participates in bounded parallel automation like any other step.

## Lifecycle

`PENDING → PLANNING → AWAITING_CAPABILITY → AUTHORIZED → RUNNING → COMPLETED`

Terminal: `FAILED | CANCELLED | UNKNOWN | BLOCKED`

## UNKNOWN

`mark_unknown` sets `auto_retry=False`. Completion of UNKNOWN tasks is blocked.

## Idempotency

`agent_task_idempotency_key(owner, tenant, parent_run, agent_type, logical_key)`

Repeated delegation with the same key returns the same task.

## Parallel automation

AGENT steps use the same `select_eligible_steps` / `execute_parallel_graph` path.
Per-run concurrency limit still applies.

## Security

- No self-grant
- No client grants
- No unrestricted shell/filesystem capability
- Cross-owner / cross-tenant access denied
- Plan ≠ evidence
- Fabricated claims ignored

## Why not a second engine

Agent tasks schedule **existing** capabilities through the substrate. SCRIPT/HTTP/DATABASE isolation and credential rules remain enforced by the sole workflow executor.
