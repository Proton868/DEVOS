# DevOS Final Acceptance Contract

**Authoritative status document for Web Intelligence + creative persona closure.**

This is not a feature specification. It freezes implementation closure criteria and validation classification rules.

---

## FINAL STATUS DEFINITIONS

Use exactly one overall status:

| Status | Meaning |
|--------|---------|
| **CLOSED** | Objectives A–B implemented and deterministically tested; required validation executable and passing |
| **CLOSED — ENVIRONMENT VALIDATION BLOCKED** | A–B implemented and deterministically tested; broader validation blocked by environment only |
| **IMPLEMENTATION INCOMPLETE** | Required implementation or deterministic tests missing/failing |
| **FAILED — REGRESSION** | Previously passing capability now fails |

Do **not** use `PARTIAL` as overall status.

For individual capabilities:

- **IMPLEMENTATION: PASS** + **LIVE VALIDATION: BLOCKED** when code is proven by tests but live/browser cannot run.

Validation result categories (exactly one each):

`PASS` | `FAIL` | `BLOCKED` | `NOT CONFIGURED` | `NOT APPLICABLE`

---

## FINAL CLOSURE OBJECTIVES

### A. Mission failure → NuhaDecision → fresh authorization → recovery execution

**Path (canonical):**

```
web_crawl attempt
→ ExecutionEvidence
→ diagnose / decide_recovery (mission_engine)
→ NuhaDecision (RETRY | REPAIR | REPLAN | ASK_USER | ABORT)
→ fresh UCIP (never reuse prior authorization)
→ Jobs / worker only if authorized
→ verification / terminal state
```

**Implementation:** `brain/web_crawl_recovery.py`  
**Mission node execution:** `brain/orchestration_runtime.py` (`_run_web_crawl_node`)  
**Decision engine:** `brain/mission_engine.py` (`decide_recovery`, `NuhaDecision`)

**Deterministic evidence required:**

| Scenario | Expected decision | Worker after decision |
|----------|-------------------|------------------------|
| timeout | `retry` | only if UCIP allows |
| UCIP deny on retry | terminal `blocked_by_ucip` | **0** |
| 401 / credential | `ask_user` | **0** |
| SSRF / private host | `abort` | **0** |
| completed crawl_id reuse | idempotent | no second enqueue |

### B. Creative persona → runtime → workspace artifact → verification

**Path:**

```
persona (writer | storyteller | script_writer)
→ Specialty Policy ∩ Capability Canon ∩ UCIP
→ Agent Runtime (test: DEVOS_ORCH_FAKE_RUNTIME=1 + pytest only)
→ files_changed / workspace claim
→ ExecutionEvidence success
```

**Registry:** `brain/personas.py`  
**Routing:** `suggest_personas_for_goal`  
**No** creative-specific runtime, workspace, or verification system.

**Production:** `DEVOS_ORCH_FAKE_RUNTIME` must never enable silent fake success outside explicit test allow.

### C. Regression / security validation

Focused + relevant suites; full `pytest -q` when environment allows.

### D. Acceptance / environment classification

Classify each stage independently (see matrix). Environment blockers do not convert implementation FAIL into BLOCKED overall if focused tests fail.

---

## EXACT TEST ENTRY POINTS

### Stage 1 — Focused closure (must all PASS before broader runs)

```bash
python3 -m pytest -q tests/test_mission_recovery_creative_e2e.py
python3 -m pytest -q tests/test_final_integration_closure.py
python3 -m pytest -q tests/test_web_cache.py
```

Combined:

```bash
python3 -m pytest -q \
  tests/test_mission_recovery_creative_e2e.py \
  tests/test_final_integration_closure.py \
  tests/test_web_cache.py
```

### Stage 2 — Relevant regression (files that exist in-repo)

```bash
python3 -m pytest -q \
  tests/test_web_crawl_platform.py \
  tests/test_web_crawl_worker.py \
  tests/test_personas_creative_web_harden.py \
  tests/test_personas_nuha.py \
  tests/test_carai_web_intel.py \
  tests/test_acceptance_final.py \
  tests/test_cancel_resume.py \
  tests/test_saga_tracing.py \
  tests/test_otel_compensation.py
```

(Omit any file not present; do not invent names.)

### Stage 3 — Full backend

```bash
python3 -m pytest -q
```

| Outcome | Classification |
|---------|----------------|
| All assertions pass | `PASS` |
| Missing import/deps (e.g. fastapi) | `BLOCKED` |
| Assertion regression | `FAIL` |

### Stage 4 — Frontend (`frontend-src/package.json`)

| Script | Status |
|--------|--------|
| `npm start` | configured (`react-scripts start`) |
| `npm run build` | configured (`react-scripts build && node scripts/deploy-frontend.mjs`) |
| `npm run deploy` | configured |
| `npm test` | **NOT CONFIGURED** |
| typecheck | **NOT CONFIGURED** |
| lint | **NOT CONFIGURED** |

If Node/npm unavailable: **BLOCKED**.

### Stage 5 — Browser

Only `PASS` if browser observes the app. Otherwise **BLOCKED**.

### Stage 6 — Live providers (public web, voice, OTel collector)

Only `PASS` when exercised. Otherwise **BLOCKED**.

---

## SECURITY INVARIANTS (executable evidence)

| Invariant | Observable proof |
|-----------|------------------|
| UCIP deny-before-runtime | `authorization_decision != allow` → runtime success false / blocked; no crawl worker |
| Recovery fresh UCIP | `force_ucip_deny=True` → `retry_authorized=False`, `worker_executed_after_decision=False` |
| ASK_USER | decision `ask_user`, worker after = 0 |
| ABORT (SSRF) | decision `abort`, worker after = 0 |
| Idempotent completed crawl | `raw_terminal.idempotent is True` |
| Persona ≠ authority | creative personas lack `deployment.production` / `vcs.push` |
| XP ≠ authorization | XP does not grant capabilities (see `docs/architecture/xp-authority-boundary.md`) |
| Cache ≠ SSRF bypass | `_safe_fetch` checks `is_url_allowed` before cache hit use; private hosts blocked |
| No production inline crawl | `api/routes/web_crawls.py` returns 503 `WORKER_UNAVAILABLE`; only `DEVOS_WEB_CRAWL_INLINE_TEST=1` for tests |

---

## FAILURE ARTIFACTS

- Tests must not write secrets, API keys, or production workspace data.
- Runtime DB files under `data/*.sqlite3` are **not** source fixtures — do not commit.
- Intentional fixtures only under `tests/fixtures/` if added.

**Cleanup:** remove temporary crawl/cache DB state created during local runs before commit.

---

## ARCHITECTURAL INVARIANT (one authority each)

| Concern | Authority |
|---------|-----------|
| Orchestration | Mission Engine + orchestration runtime |
| Authorization | UCIP ∩ Specialty Policy ∩ Capability Canon |
| Execution | Agent Runtime / Jobs |
| Workspace | FileService |
| Verification / evidence | existing verification + ExecutionEvidence |
| Recovery vocabulary | `NuhaDecision` / `decide_recovery` |
| Web crawl | `execution/web_intel` crawler + Jobs `web_crawl` |
| Web cache | `execution/web_intel/cache.py` (SQLite) |
| Personas | `brain/personas.py` registry |
| Observability | TraceContext / OTel bridge |
| XP | experience only |

**Forbidden:** second recovery engine, production fake runtime, production inline crawl, authorization reuse on recovery, UCIP bypass by Nuha/persona/XP.

---

## STOP RULE

When:

- Closure A = PASS  
- Closure B = PASS  
- Security invariants = PASS (focused evidence)  
- Focused suites = PASS  
- Architectural audit = PASS  

and remaining items are only environment/live **BLOCKED**:

**STOP.** No further product features on this subsystem under this contract.
