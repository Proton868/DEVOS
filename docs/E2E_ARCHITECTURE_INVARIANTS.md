# DevOS end-to-end architecture invariants (Cluster 11)

Baseline HEAD at documentation time is recorded in git history.

## Intended chain

```text
request
  → authenticated identity (get_current_user)
  → ownership (session/mission/workspace scoped to user.id)
  → intent (Nuha classification)
  → execution plan (create_plan)
  → governance (UCIP / specialty / HITL)
  → capability / tool (AgentRuntime)
  → provider (OmniRoute via BrainLLM)   [model text only]
  → result / artifact (FileService)
  → Ponytail + evidence
  → mission_acceptance → mission_truth
  → audit/outbox events + telemetry
  → response (SSE)
```

## Invariant status (code-level)

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Authentication ≠ authorization | **PASS (code)** | JWT establishes user; UCIP/FileService enforce ops |
| 2 | Observability ≠ authorization | **PASS (code)** | Traces/logs do not call UCIP |
| 3 | Audit ≠ telemetry | **PASS (code)** | Evidence/outbox vs tracing modules |
| 4 | Trace does not grant permission | **PASS (code)** | No auth path reads span success |
| 5 | Tool cannot bypass governance | **PASS (code)** | AgentRuntime UCIP gate before tool |
| 6 | Provider response cannot grant permission | **PASS (code)** | mission_acceptance + ProviderExhaustedError |
| 7 | Client cannot override server identity | **PASS (code)** | ChatReq has no user_id; path uses user.id |
| 8 | Failures ≠ successful authorization | **PASS (code)** | UCIP DENY continues without side effect |
| 9 | Side effects after governance gate | **PASS (code)** | Tools after UCIP.request |
| 10 | Destructive ops retain HITL/confirm | **PASS (code)** | HITL_REQUIRED_CAPS / critical plan deny |
| 11 | Meaningful execution has provenance | **PARTIAL** | A2A + evidence when path completes; live UNVERIFIED |
| 12 | Secrets redacted before durable persistence | **PASS (code)** | secrets_redact / evidence scrub |
| 13 | Cleanup ≠ successful telemetry | **PARTIAL** | process lifecycle separate; live UNVERIFIED |
| 14 | Cross-user access prevented | **PASS (code)** | sessions, FileService scope, acceptance owner |
| 15 | Async preserves security model | **PARTIAL** | background plan task uses same user_id; live UNVERIFIED |

## Production (prime) — UNVERIFIED in CI/sandbox

Run on the production host after deploy:

```bash
cd ~/devos && git pull origin main
DEVOS_DEPLOY_MODE=production ./ops/update.sh
./ops/verify.sh --production
# Authenticated Nuha mission; inspect mission_id, A2A, Ponytail, evidence
# Cross-user IDOR checks on /api/files and mission APIs
```
