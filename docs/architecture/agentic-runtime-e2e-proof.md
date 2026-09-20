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


## Capability Catalog vs Authorization

| Layer | Role |
|-------|------|
| **Capability Catalog** | What exists (metadata, schemas, risk, isolation, evidence requirements) |
| **Task Projection** | What this task may *consider* (policy + allowed list) |
| **UCIP Authorization** | What this task may *actually execute* |
| **ExecutionOperation/Job** | What actually happened |
| **Evidence** | What proves it happened |

Invariants:

- Discovery is not authorization.
- Planner-supplied capability metadata is never authoritative.
- Catalog describes. Projection constrains. Planner proposes. Runtime validates.
  UCIP authorizes. Operation/Job records. Isolation constrains. Evidence proves.

See `governance/capability_catalog.py`.


## Workspace & Artifact Capabilities

Family: `workspace.list`, `artifact.stat`, `artifact.read`, `artifact.write`, `artifact.delete`

| Concern | Policy |
|---------|--------|
| Workspace identity | From trusted task context (`owner_id` + `project_id` metadata) |
| Paths | Relative only; no absolute, `..`, encoded traversal, null bytes |
| Symlinks | Fail-closed (read/write/delete refused) |
| Size limits | Hard ceilings on list/read/write |
| Secrets | `.env` / key-like paths: content denied |
| Digests | SHA-256 computed by trusted implementation |
| Concurrency | `expected_digest` precondition → CONFLICT |
| Consequential | write/delete remain UCIP + Operation/Job path |

Invariant: Artifact capability ≠ filesystem access. Planner proposes; runtime validates; UCIP authorizes.

See `execution/workspace_capabilities.py`.


## project.validate

Governed validation capability. Planner selects only an allowlisted **profile**:

| Profile | Behavior |
|---------|----------|
| `structure` | Static workspace inspection via FileService (no process) |
| `verify` | Structure + recognized project markers |
| `test` | Existing runtime lifecycle `test` action (isolated) |

```
Agent → Catalog → Runtime → UCIP → Operation/Job → Runtime/Isolation → Evidence → Observation
```

Planner cannot supply: command, shell, argv, workspace_root, env, isolation, success/evidence forgeries.

See `execution/project_validate.py`.


## Governed Agentic Build/Repair Proof

Fixture: `tests/fixtures/agentic_repair_project/`

Initial defect: `src/calculator.js` returns `a - b` while tests expect addition.
Content contract: `devos.validate.json` requires `return a + b` and forbids `return a - b`.

Loop:

1. `artifact.read` — observe defect through governed path
2. `project.validate` profile=`test` — trusted **failure**
3. `artifact.write` — bounded repair (`return a + b`)
4. `project.validate` profile=`test` — trusted **success**
5. Completion gate — only after evidence

### Acceptance matrix

| ID | Acceptance | Required |
|----|------------|----------|
| AC-01 | Initial defect produces trusted failure | YES |
| AC-02 | Agent reads through artifact capability | YES |
| AC-03 | Repair uses bounded artifact.write | YES |
| AC-04 | Artifact digest changes | YES |
| AC-05 | Agent validates after repair | YES |
| AC-06 | Result comes from trusted validator | YES |
| AC-07 | Failure forces another turn | YES |
| AC-08 | Second validation succeeds | YES |
| AC-09 | Completion requires proof | YES |
| AC-10 | Premature completion rejected | YES |
| AC-11 | No shell bypass | YES |
| AC-12 | Identity remains trusted | YES |
| AC-13 | UNKNOWN remains unresolved | YES |
| AC-14 | Idempotency remains governed | YES |
| AC-15 | Idempotent repair replay is stable | YES |
| AC-16 | Concurrent repair converges safely | YES |

See `tests/test_agentic_build_repair.py`.


## Governed Project Build Proof

Capability: `project.build` (profile=`build` only)

Fixture: `tests/fixtures/agentic_build_project/`
Contract: `devos.build.json` → artifact `dist/app.js`

```
Agent → Catalog → project.build → Runtime → UCIP → Operation/Job
  → Runtime/Isolation or content-build → Real Artifact → Evidence → Observation → Completion
```

| ID | Acceptance | Required |
|----|------------|----------|
| AC-01 | Capability discovery | YES |
| AC-02 | Bounded profile | YES |
| AC-03 | Trusted project identity | YES |
| AC-04 | Isolated / no host bypass | YES |
| AC-05 | Valid build succeeds | YES |
| AC-06 | Real artifact exists | YES |
| AC-07 | Artifact project-scoped | YES |
| AC-08 | Trusted digest | YES |
| AC-09 | Trusted build evidence | YES |
| AC-10 | Structured observation | YES |
| AC-11 | Build failure is real | YES |
| AC-12 | Failure cannot complete | YES |
| AC-13 | Successful build can complete | YES |
| AC-14 | Replay/idempotency | YES |
| AC-15 | Concurrent build convergence | YES |
| AC-16 | UNKNOWN cannot complete | YES |
| AC-17 | Artifact integrity after replay | YES |
| AC-18 | No arbitrary execution surface | YES |

See `tests/test_agentic_build.py` and `execution/project_build.py`.


## Governed Project Preview / Runtime Proof

Capability: `project.preview` (profile=`preview` only)

**BUILD: complete · PREVIEW/RUNTIME: this milestone · VERIFY: next · DEPLOY: not implemented**

```
trusted build → project.preview → authorize → session/runtime_service
  → durable runtime identity → readiness evidence → observation → completion
```

- Identity from trusted task context only
- Build artifact prerequisite (not planner claims)
- Session mode: durable `.devos/preview_session.json` (deterministic)
- Runtime mode: `runtime_service` start (isolation required; fail-closed)
- No planner command/port/path/ready/evidence fields
- Idempotent same-build session reuse
- Cross-owner/project isolation
- Stop is project-scoped (no arbitrary PID kill)

See `execution/project_preview.py` and `tests/test_agentic_preview.py`.


## Governed Project Verify Proof

Capability: `project.verify` (profile=`verify` only)

**Lifecycle: CREATE…PREVIEW complete · VERIFY this milestone · DEPLOY not implemented**

Central rule: **READY ≠ VERIFIED**

```
build → preview → project.verify → application checks → evidence → VERIFIED|FAILED
```

- Prerequisites: trusted build artifact + READY preview session (owner/project scoped)
- Application checks from `devos.verify.json` (must_contain, forbid_contains, exports)
- Session READY alone never yields VERIFIED
- Session mode does not claim live network/runtime
- No planner command/port/ready/verified/evidence fields
- Durable `.devos/verify_result.json`

See `execution/project_verify.py` and `tests/test_agentic_verify.py` (AC-25..AC-48).


## Governed Project Deploy Proof

Capability: `project.deploy` (profile=`deploy`)

**SCOPE GATE:** Deploy manages a **bounded deployment target** only.
Not responsible for cloud/DNS/TLS/K8s/Terraform/IAM/autoscaling/CDN/billing.

Supported:
- target_type: `managed_session`
- environment: `preview`

```
build → preview → verify → project.deploy → deployment identity → evidence → DEPLOYED
```

- Verified artifact binding + immutability (ARTIFACT_MISMATCH)
- Idempotent same artifact/target
- No destructive auto-replace of active different-artifact deployment
- Shared `governance/security_policy.py`
- No fake external endpoints

Lifecycle: CREATE…VERIFY complete · **DEPLOY complete** · OBSERVE/MAINTAIN implemented later

AC-71 UNKNOWN: crash after deploy side effect / before terminal cannot become COMPLETED; auto_retry remains false.

See `execution/project_deploy.py` and `tests/test_agentic_deploy.py` (AC-49..AC-80).


## Governed Project Observe Proof

Capability: `project.observe` (profile=`observe`, read-only)

**Three domains (independent):**
- Task status — agentic completion gate
- Operation status — capability execution (SUCCEEDED may accompany UNHEALTHY observation)
- Observation status — NOT_OBSERVED | OBSERVING | OBSERVED | UNHEALTHY | UNREADY | DEGRADED | UNAVAILABLE | FAILED

Contract: `devos.observe.json` (version=1, declarative checks only)

Allowed check kinds: runtime_state, readiness, health, artifact_identity, deployment_identity

Session-mode honesty: no fabricated live HEALTHY/READY process health.

Not provided: shell, network probes, log scraping, metrics, alerts, remediation, infrastructure.

Lifecycle: CREATE…DEPLOY complete · **OBSERVE complete** · MAINTAIN complete · INCIDENT foundation complete

See `execution/project_observe.py` and `tests/test_agentic_observe.py` (AC-81..AC-120).


## Governed Project Maintain Proof

Capability: `project.maintain` (profile=`maintain`)

**Additional domains (independent of Task / Operation / Observation):**
- Maintenance Request — DETECTED … RESOLVED / FAILED / CANCELLED / UNKNOWN / …
- Maintenance Action — PENDING … SUCCEEDED / FAILED / SKIPPED / UNKNOWN / …

**Invariant:**

> Observation describes the system.
> Task status describes the agent.
> Operation status describes capability execution.
> Maintenance request/action describe governed repair intent and bounded work.

Intended loop:

```text
OBSERVE → observed state → governed maintenance decision
  → existing capability → operation → OBSERVE again → evidence
```

Contract: `devos.maintain.json` (version=1, declarative policies only)

- Trigger source: `observation` only
- Actions: catalogued capabilities only (`project.build`, `project.verify`, `project.observe`, …)
- Verification ordering: `after` primary action
- Required vs optional actions are durable
- Authorization is separate from observation (observation is evidence, not authority)

Not provided:
- second runtime / execution engine / evidence store
- monitoring platform or alerting
- autonomous remediation loops
- arbitrary shell/URL/port/code in the contract
- incident paging (see Incident domain foundation)

See `execution/project_maintain.py` and `tests/test_agentic_maintain.py` (AC-121..AC-182).


## Governed Incident Domain Foundation

Capability: `project.incident` (governance/read-oriented)

Sixth independent domain: Incident lifecycle (DETECTED … RESOLVED).

Observation may *detect* an incident under `devos.incident.json` policy.
Incident may *link* to a Maintenance Request.
Mitigation still flows only through Maintain + existing capabilities.
No auto-remediate, pager, or shell path.

See `execution/project_incident.py` and `tests/test_agentic_incident.py` (AC-183..AC-220).
