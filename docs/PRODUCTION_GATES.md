# DevOS Production Gates

Labels: **IMPLEMENTED** · **TESTED** · **LIVE-VERIFIED** · **UNPROVEN**

Do not claim production ready until LIVE-VERIFIED on the real host.

## Authentication
| Gate | Status |
|------|--------|
| Authenticated chat | IMPLEMENTED |
| Authenticated execution | IMPLEMENTED |
| Ownership isolation | IMPLEMENTED |
| Cross-user denial | TESTED (partial) |

## Execution spine
| Gate | Status |
|------|--------|
| Nuha → Mission → DAG → UCIP → AgentRuntime | IMPLEMENTED |
| Mission truth final authority | IMPLEMENTED + TESTED |
| Verification before node COMPLETED | IMPLEMENTED + TESTED |
| DIRECT_SCAFFOLD ≠ MISSION_EXECUTION | IMPLEMENTED + TESTED |
| Plan idempotency key | IMPLEMENTED + TESTED |

## Streaming
| Gate | Status |
|------|--------|
| Chat SSE first byte early | IMPLEMENTED |
| Sequenced plan events | IMPLEMENTED + TESTED |
| Event replay `?after=N` | IMPLEMENTED |
| Background `/run` | IMPLEMENTED |
| Live no-504 under proxy | **UNPROVEN** |

## Cancellation
| Gate | Status |
|------|--------|
| request_cancel + cascade | IMPLEMENTED |
| Agent task cancel flag | IMPLEMENTED |
| Subprocess kill on cancel | IMPLEMENTED |
| Cancel is terminal for mission_truth | IMPLEMENTED + TESTED |
| Live process-tree proof | **UNPROVEN** |

## HITL
| Gate | Status |
|------|--------|
| Persist approval | IMPLEMENTED + TESTED |
| Decide APPROVED/DENIED | IMPLEMENTED + TESTED |
| Resume same plan | IMPLEMENTED |
| Survive browser disconnect | IMPLEMENTED (same process) |
| Survive API restart | PARTIAL (file-backed) / **UNPROVEN** live |

## Truth
| Gate | Status |
|------|--------|
| False success prevented | IMPLEMENTED + TESTED |
| Verification failure → not SUCCEEDED | IMPLEMENTED + TESTED |
| Prefer unknown over false Done | IMPLEMENTED |

## Health
| Gate | Status |
|------|--------|
| Backend `/api/health` | IMPLEMENTED |
| Public DevOS health (not wrong service) | **UNPROVEN** |

## Reference
See [NUHA_RUNTIME.md](NUHA_RUNTIME.md) for the canonical spine.

## Live probe log (sandbox agent — 2026-09-09)

Environment: **not** production host (`/home/ubuntu/DEVOS` absent, no systemd).

| Probe | Result | Evidence |
|-------|--------|----------|
| `GET https://devos.carai.agency/api/health` | **FAILED** | HTTP 404 HTML **Page Not Found - PyRunner** (Cloudflare). Not DevOS JSON. |
| Authenticated SSE / multi-node / cancel tree / HITL restart | **UNPROVEN** | No prime shell; no token |

### Operator command (on prime)

```bash
export DEVOS_BASE_URL=https://devos.carai.agency   # or http://127.0.0.1:8000
export DEVOS_TOKEN=<jwt>
python scripts/live_production_proof.py
```

Public `/api/health` MUST return JSON with `"service": "devos"`. Fix nginx upstream if PyRunner is served.

## Operator-verified (Prime) — snapshot

See **docs/CURRENT_STATUS.md** for the full table. Auth E2E and `/api/health` were operator-verified at commit `aa15b22`. Cluster 12 is **not** release-green. Isolation remains `network_only` / not suitable for untrusted code.

