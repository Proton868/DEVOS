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
