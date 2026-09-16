> Canonical product vision: [`plans/DEVOS_PRODUCT_VISION.md`](../plans/DEVOS_PRODUCT_VISION.md).
>
> **Status vocabulary:** IMPLEMENTED · PARTIALLY IMPLEMENTED · TESTED BUT NOT LIVE-PROVEN · NOT IMPLEMENTED · BLOCKED · FUTURE / RESEARCH · SKIPPED
>
> **Rule:** Test coverage is evidence of tested behavior, not by itself evidence of deployed production success. Never claim production-ready from unit tests alone.

# DevOS — current operational status

This document is a **truthful snapshot** of where DevOS is *today*.  
It is not a marketing brief and not a promise that all gates have been live-proven.

---

## Product identity

DevOS is an **AI-native Development Operating System** / unified development workspace where humans and AI agents **build, run, orchestrate, inspect, improve, and deploy** software together.

Core lifecycle:

```text
BUILD → RUN → ORCHESTRATE → INSPECT → DEPLOY
```

Nuha chat is an **entry surface**, not the entire product.

---

## Architecture implemented vs production verified

| Layer | Architecture in code | Production verified |
|-------|----------------------|---------------------|
| Spatial UI / IDE / files / terminal | PARTIALLY IMPLEMENTED | Environment-dependent |
| Nuha orchestration bridge | PARTIALLY IMPLEMENTED | TESTED BUT NOT LIVE-PROVEN |
| Mission / DAG / specialty policy | PARTIALLY IMPLEMENTED | TESTED BUT NOT LIVE-PROVEN |
| UCIP / capability governance | IMPLEMENTED (policy surface) | PARTIAL live use |
| AgentRuntime + tools | PARTIALLY IMPLEMENTED | TESTED BUT NOT LIVE-PROVEN |
| Evidence / Mission Truth / verification | PARTIALLY IMPLEMENTED | TESTED BUT NOT LIVE-PROVEN |
| OmniRoute default gateway | PARTIALLY IMPLEMENTED | Not fully live-proven end-to-end |
| Postgres / Supabase SoT | IMPLEMENTED direction | Requires real deployed DB |
| Continuous learning / self-dev | FUTURE / RESEARCH | — |

---

## Human IDE path vs agent mission path

### Human IDE path (legitimate, non-agent)

Authenticated humans may directly:

- create / edit / delete project files
- use terminal / PTY
- use IDE assist endpoints
- inspect runtime, previews, settings

These are **human-controlled** DevOS operations. They are **not** automatically agent missions and must not be reported as specialist-agent accomplishment.

### Agent mission path (autonomous / delegated)

```text
USER → NUHA → EXECUTION PLAN → SPECIALIST AGENT → CAPABILITY
  → UCIP / GOVERNANCE → RUNTIME → EVIDENCE → VERIFICATION → NUHA → USER
```

Human direct control and agent-controlled execution are **complementary** but must remain **attributable and distinguishable** (security, provenance, UX, truthful reporting).

---

## Website generation (honest status)

| Item | Status |
|------|--------|
| A2A / specialist website path | PARTIALLY IMPLEMENTED |
| Website artifact validation | PARTIALLY IMPLEMENTED |
| Scaffold fallback (`scaffold_website_artifacts`, env-gated) | DEPRECATED / EMERGENCY-ONLY / **not** production agent success |
| Live specialist website missions on deployed system | TESTED BUT NOT LIVE-PROVEN (or BLOCKED by env) |

An artifact written by `scaffold_website_artifacts()` is **not** evidence that a specialist website agent successfully completed a mission.

---

## Lifecycle status table

| Lifecycle | Current status |
|-----------|----------------|
| BUILD | PARTIALLY IMPLEMENTED |
| RUN | PARTIALLY IMPLEMENTED |
| ORCHESTRATE | PARTIALLY IMPLEMENTED / TESTED BUT NOT LIVE-PROVEN |
| INSPECT | PARTIALLY IMPLEMENTED |
| DEPLOY | PARTIALLY IMPLEMENTED / largely NOT LIVE-PROVEN |
| LEARN | FUTURE / RESEARCH |
| SELF-DEVELOP | FUTURE / RESEARCH |

---

## Storage

| Mode | Role |
|------|------|
| **Postgres / Supabase** | Intended **production** source of truth |
| SQLite | Limited **development / constrained** scenarios only — not the production architecture |

---

## OmniRoute

OmniRoute is the **native/default** model-routing layer. Other providers remain configurable.  
UI/provider selection edge cases may still be **PARTIALLY IMPLEMENTED** relative to full consistency.

---

## Continuous learning

**Foundations (not full lifecycle):** persona XP, memory, evidence/audit, agency evolution hooks, mission/saga/outbox events.

**Target lifecycle (FUTURE / RESEARCH):**  
capture → evaluate → candidate → validate → govern → promote → reuse → measure

XP and learning **never** grant security authority. UCIP remains the authority boundary.

---

## MCP boundary

- **Internal:** prefer Nuha → Agent → native DevOS capability.  
- **External:** Nuha/Agent → MCP → external application/service.  
MCP filesystem-style presets are not the preferred architecture when a native capability exists.

---

## Major gaps (from architecture drift audit)

1. Optional website scaffold fallback must not be treated as agent success.  
2. Human file/terminal paths are not agent missions.  
3. End-to-end live mission proof (Nuha → UCIP → AgentRuntime → verified artifacts) incomplete on production.  
4. Deploy / push / external side-effects not live-proven as a unified lifecycle.  
5. Provider UI edge cases vs OmniRoute default.  
6. Governed skill promotion not implemented.

---

## Install note

Local install scripts may still default to SQLite for convenience. That does **not** redefine production architecture. Production deployments should use Postgres/Supabase as SoT and verify health, auth, and mission gates on the live host.

See also: [PRODUCTION_GATES.md](PRODUCTION_GATES.md), [NUHA_RUNTIME.md](NUHA_RUNTIME.md), [plans/GAP_ANALYSIS.md](../plans/GAP_ANALYSIS.md).


---

## Security / architecture cluster history (repo commits)

| Cluster | Commit | Summary | Sandbox tests (reported) |
|---------|--------|---------|---------------------------|
| 3 | `81b44c1` | web_intel durable Postgres persistence | — |
| 4 | `5f6f57c` | Canonical AuditLogger / ObservabilityStore / tracing | 5 passed |
| 5 | `588ee2b` | Unified redaction; EvidenceChain scrub on save | 40 passed |
| 6 | `c9243ee` | UCIP before side effects; ownership; capability clamp | 22 passed |
| 7 | `d0680ee` | Resource lifecycle (tasks, subprocess, SSE, pool) | 5 passed |
| 8 | `99fc866` | Evidence/graph/trace/memory ownership APIs | 4 passed |
| 9 | `c554669` | Provider reliability; fail-closed exhaustion; OmniRoute | 22 passed |
| 10 | `060e70f` | Workspace path / artifact extraction security | 24 passed |
| 11 | `7d0ca87` | E2E governance-chain architecture tests | 36 passed |
| 12 | _(no code commit)_ | Sandbox release-gate review only | 58 focused passed; **not** release-green |

Cluster 11 explicitly left several **live** production gates **UNVERIFIED**.

Cluster 12 is a **sandbox** release-gate review only. Full async provider suite was limited by the test environment. **Do not treat Cluster 12 as production-green.**

---

## Production verification on Prime (operator-verified)

These facts were verified by the operator on the production VPS **prime**. They are **not** claims from the coding sandbox.

### Auth (commit `aa15b22` / `aa15b2260414d68a87dd68bdb3bbd68e17500aba`)

End-to-end auth path **PASS**:

```text
Supabase password authentication
  → Supabase access token
  → POST /api/auth/supabase/exchange
  → DevOS local JWT
  → authenticated GET /api/auth/me
```

Verified account (email only): `caraiagency@gmail.com`

Audit fix: successful login events use canonical **`AuditEventType.AUTH`** (not a nonexistent `LOGIN_SUCCESS` enum member).

### Health (`GET /api/health` after `aa15b22`)

Operator-verified values:

| Field | Value |
|-------|--------|
| service | devos |
| status | ok |
| db | ok |
| db_backend | postgres |
| memory | ok |
| memory_backend | postgres |
| default_provider | omniroute |
| execution_store_backend | postgres |
| outbox_backend | postgres |
| saga_backend | postgres |
| audit_backend | postgres |
| governance | v1-frozen |
| ucip | ok |
| orchestration_store | ok |
| mission_runtime.agent_runtime | import_ok |
| mission_runtime.fake_runtime_env | false |

Isolation (reported): `backend=unshare`, `strength=network_only`, `suitable_for_untrusted_code=false`.

**Do not interpret this isolation result as production-safe untrusted-code execution.**

### Tenant model alignment (repo)

Migration restored in repository:

- File: `supabase/migrations/20260916171447_tenant_model_alignment.sql`
- Commit: `cbad97808c09b9c85be31f3cfe786a4d616a2df4`
- Adds: `tier`, `is_active`, `metadata`

Remote Supabase already had this migration version applied separately. The repo file was restored for **reproducibility**. Coding agents did **not** apply this migration remotely in the sandbox.

---

## Still requires live verification

Unless the repository contains **new** operator evidence, the following remain **UNVERIFIED** on production:

- Full production deploy/reload under load
- Complete migration/RLS matrix on live DB
- Full OmniRoute live multi-model behavior
- Artifact + provenance end-to-end under real missions
- Audit/trace persistence under real missions
- Resource lifecycle under sustained workload
- Deployment pipeline execution
- Cross-user IDOR against the live environment
- Any gate not listed above as operator-verified

Language rule: **sandbox test pass ≠ production release green**.


---

## Lifecycle progress streaming (post-UI cluster)

**Gap found:** After SSE `delegating`, `run_delegated_mission` ran as a blocking black box. Intermediate statuses expected by `NuhaTaskProgress` (`agent_progress`, `worker_completed`, `validation_started`) were **not** emitted, so the UI could not show real specialist progress.

**Fix (repo):** Optional `on_progress` callback on `run_delegated_mission` fires only at real checkpoints (mission/task assigned, A2A sent, specialist execution result, Ponytail start). Chat streams those events over SSE via a progress queue.

| Claim | Status |
|-------|--------|
| Progress events tied to real delegation checkpoints | LOCALLY TESTED (source + `_emit_progress` unit) |
| End-to-end live specialist mission on Prime | STILL UNVERIFIED |
| Artifact/evidence acceptance under load | STILL UNVERIFIED |


---

## Specialist / AgentRuntime path (cluster after progress SSE)

### Actual path (code)

```text
chat → create_plan → run_delegated_mission
  → _run_agent_node → run_node_on_agent_runtime
  → AgentRuntime (provider=DEFAULT_PROVIDER / omniroute)
  → BrainLLM.stream_chat → OmniRoute
  → UCIP-gated tools → workspace files
  → Ponytail → evidence → mission acceptance → SSE
```

Fake runtime only when `DEVOS_ORCH_FAKE_RUNTIME=1` **and** test allow (`PYTEST_CURRENT_TEST` or `DEVOS_ALLOW_FAKE_RUNTIME=1`).

### Proof status

| Item | Status |
|------|--------|
| Code path AgentRuntime + explicit DEFAULT_PROVIDER | LOCALLY TESTED |
| Progress SSE checkpoints | LOCALLY TESTED (prior cluster) |
| Live OmniRoute specialist mission on Prime | **STILL UNVERIFIED** — see `docs/PRIME_SPECIALIST_MISSION_VERIFY.md` |
| Production proof of artifact + evidence under load | **STILL UNVERIFIED** |

