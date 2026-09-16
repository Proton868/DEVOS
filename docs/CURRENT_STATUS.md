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
