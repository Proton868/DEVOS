# DevOS Production E2E Acceptance Record

**Document purpose:** Permanent record of what has been **proven** versus what remains **unproven** for the governed mission lifecycle.

| Field | Value |
|-------|--------|
| **Tested commit (Phase 3 baseline)** | `528754d749d044fd65bbf066c95b0f2ae38ef359` |
| **Document commit** | *(set at push time)* |
| **Environment type** | Coding-agent sandbox (not production VPS `prime`) |
| **Date/time (UTC)** | 2026-09-16 13:45:17 UTC |
| **Architecture under test** | Phase 1 spine + Phase 2 fail-closed acceptance + Phase 3 durability |

---

## Intended lifecycle

```text
Authenticated Human
  → Nuha → Intent → Mission → Execution Plan
  → A2A Delegation → Specialist → AgentRuntime → UCIP
  → Real Execution → Artifact → Ponytail → Evidence
  → Mission Acceptance → Mission Truth → Nuha Synthesis → Human
```

---

## Environment capability (this run)

| Check | Result |
|-------|--------|
| Production VPS / systemd | **UNPROVEN** — systemd offline; no `devos.service` |
| `GET /api/health` (127.0.0.1:8000) | **UNPROVEN** — service unreachable |
| Postgres / Supabase live | **UNPROVEN** — no production `DATABASE_URL` in this environment |
| OmniRoute (127.0.0.1:3000) | **UNPROVEN** — unreachable |
| Real JWT / dual auth session | **UNPROVEN** |
| `ops/update.sh` / `ops/verify.sh --production` | **UNPROVEN** — not the production host |
| Scaffold disabled by default | **PASS** (unit: Phase 1/2 tests + code path) |

Secrets (JWT, DATABASE_URL, API keys) were **not** available and are **not** recorded here.

---

## Automated regression (repository)

Command:

```bash
PYTHONPATH=. python3 -m pytest \
  tests/test_mission_spine_phase1.py \
  tests/test_governance_phase2.py \
  tests/test_durability_phase3.py \
  -q
```

**Result:** **23 passed** (on sandbox Python 3.12).

These prove **code invariants**, not live production execution.

---

## Acceptance criteria matrix

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | Real authenticated user/session | **UNPROVEN** | No production auth host |
| 2 | Real Postgres mission persistence | **UNPROVEN** | |
| 3 | One mission per orchestrated request | **PASS** (unit) / **UNPROVEN** (live) | Phase 1 AST + bridge single-path tests |
| 4 | No competing `execute_plan` from chat | **PASS** | `tests/test_mission_spine_phase1.py` |
| 5 | A2A durable records with correlation | **PASS** (unit stable IDs) / **UNPROVEN** (live DB) | |
| 6 | AgentRuntime + UCIP real tool execution | **UNPROVEN** | Needs runtime + provider |
| 7 | Real workspace artifacts (non-scaffold) | **UNPROVEN** | |
| 8 | Ponytail gate executed | **PASS** (policy unit) / **UNPROVEN** (live) | Phase 2 |
| 9 | Durable evidence row | **UNPROVEN** (live) / **PASS** (required by acceptance unit) | |
| 10 | Mission truth only after acceptance | **PASS** | Phase 2 suite |
| 11 | `explicit_ok` cannot bypass | **PASS** | Phase 2 |
| 12 | Crash after artifact → resume Ponytail | **PASS** (reconcile unit) / **UNPROVEN** (live restart) | Phase 3 |
| 13 | Crash after Ponytail+evidence → complete | **PASS** (reconcile unit) / **UNPROVEN** (live) | |
| 14 | Crash before evidence → NOT success | **PASS** (unit) | |
| 15 | Duplicate idempotency key → one mission | **PASS** (key scoping unit) / **UNPROVEN** (live concurrent) | Owner-scoped keys |
| 16 | Cross-owner isolation | **UNPROVEN** (live) | Existing authz tests exist in repo but not run fully here |
| 17 | UCIP deny unauthorized/unknown | **UNPROVEN** (live runtime) | Soft plan authorize path removed (Phase 2) |
| 18 | Scaffold cannot succeed in production | **PASS** | Default/prod disabled |
| 19 | OmniRoute real completion | **UNPROVEN** | |
| 20 | `ops/verify.sh --production` | **UNPROVEN** | |

---

## Mission trace (template for live proof)

When run on prime, fill redacted IDs:

| Step | Identifier (redacted) | Observed |
|------|----------------------|----------|
| user_id | `user_***` | |
| mission_id | `mis_***` | |
| plan_id | `plan_***` | |
| task_id | `task_***` | |
| idempotency_key | `chat:***` | |
| a2a_message_ids | `a2a_***` | |
| artifact paths | e.g. `index.html` | |
| ponytail.passed | true/false | |
| evidence_id | `ev_***` | |
| final status | succeeded/failed | |

**This sandbox run:** no live mission IDs — **UNPROVEN**.

---

## Known limitations

1. Live production proof requires access to the DevOS VPS, Postgres, OmniRoute, and secrets.
2. Full pytest suite beyond Phase 1–3 was not claimed green in this environment.
3. Crash recovery is unit-validated via `reconcile_mission_state`; process kill/restart on systemd is **UNPROVEN**.
4. Concurrent DB unique-index behavior under load is **UNPROVEN** without Postgres.

---

## How to complete the live proof (on production host)

```bash
cd ~/devos
git fetch origin && git checkout main && git reset --hard origin/main
DEVOS_DEPLOY_MODE=production ./ops/update.sh
sudo systemctl status devos --no-pager
./ops/verify.sh --production
# Authenticated chat: "Build a simple one-page shoe store website..."
# Record mission_id, A2A ids, Ponytail, evidence; update this document.
```

---

## Governing principle

> Prove DevOS works as one governed operating system, not as a collection of passing components.

**Overall Phase 4 status for this environment:**  
**PARTIAL** — architectural regressions **PASS**; full production-like E2E **UNPROVEN**.
