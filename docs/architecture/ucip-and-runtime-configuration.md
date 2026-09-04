# UCIP Authorization & Runtime Configuration Audit

**Repo HEAD baseline:** `3451603` (+ hardening in this commit)  
**Scope:** authorization path, capability canon, specialty policy, env config — **no parallel systems**.

---

## 1. Authorization path (actual code)

```text
Nuha / Mission node
  → node.capabilities (short names or ucip:*)
  → brain.capability_canon.canonicalize / specialty_policy._canon
  → brain.specialty_policy.evaluate_node_request
       (allow ∩ deny ∩ ALWAYS_BLOCKED ∩ trust ceiling ∩ HITL hints)
  → if deny: node BLOCKED — Agent Runtime NEVER called
  → if allow: brain.orchestration_runtime.run_node_on_agent_runtime
  → brain.agent_runtime.AgentRuntime
       → UCIPGateway / require_authority per tool action
       → execution tools + FileService workspace
```

| Step | Module | Role |
|------|--------|------|
| Persona / node caps | `mission_engine.dispatch_node`, `orchestration` | Request surface |
| Canonicalization | `brain/capability_canon.py` | Alias → canonical; **never expands authority** |
| Specialty Policy | `brain/specialty_policy.py` | Declarative persona allow/deny; **not** a second auth engine |
| UCIP constants | `governance/ucip.py` | `ALWAYS_BLOCKED_CAPS`, `HITL_REQUIRED_CAPS`, `TRUST_LEVEL_CAPS` |
| UCIP gateway | `UCIPGateway.request` | Per-tool gate inside Agent Runtime |
| Runtime boundary | `orchestration_runtime.py` | Deny → no `AgentRuntime` |

### Who / what / where

| Question | Implementation |
|----------|----------------|
| **WHO** | `AgentIdentity` (`user_id`, `session_id`, `agent_id`, trust, capability set) |
| **WHAT** | Capability string (`ucip:filesystem.write`, …) or tool `action` mapped via `ACTION_TO_CAP` |
| **WHERE** | Workspace / `project_id` on AgentRuntime + FileService; specialty `scope_paths` are declarative guidance |
| **WHY** | Orchestration `plan_id` / `node_id` in objective and events |
| **RISK** | Plan/node `risk_level`; specialty `allow_risk` / `deny_risk`; HITL on critical |
| **TRUST** | `TrustLevel` + `TRUST_LEVEL_CAPS` intersection |
| **HITL** | `HITL_REQUIRED_CAPS` + specialty `require_hitl` → `AWAITING_APPROVAL` / `waiting_for_user` |

**Invariant:** Specialty Policy ∩ UCIP. One allow is not enough.

---

## 2. Capability canonicalization

Aliases (compatibility only):

| Alias | Canonical (orchestration) | UCIP form (`to_ucip`) |
|-------|---------------------------|----------------------|
| `fs.read` | `filesystem.read` | `ucip:filesystem.read` |
| `fs.write` | `filesystem.write` | `ucip:filesystem.write` |
| `shell.exec` | `shell.execute` | `ucip:execution.bash` |
| `ucip:filesystem.read` | `filesystem.read` | `ucip:filesystem.read` |

- Same authority for alias and UCIP form (`aliases_are_same_authority`).
- Unknown capabilities fall through; specialty allow-list / trust ceiling deny them.
- Global `ALWAYS_BLOCKED_CAPS` applied on UCIP names after mapping.

---

## 3. Deny-before-runtime

`dispatch_node` / orchestration policy eval:

```text
decision.allow == False
  → node BLOCKED
  → return without run_node_on_agent_runtime
```

`run_node_on_agent_runtime`:

```text
authorization_decision != "allow"
  → status blocked, no AgentRuntime
```

Tests: specialty research+shell, production.delete, runtime counter = 0 on deny.

---

## 4. Replan authorization

`apply_revision` injects **new** nodes without copying `authorization_decision`.  
Each new node goes through `evaluate_node_request` + runtime path again.  
**No inheritance of prior node ALLOW.**

---

## 5. Workspace boundary

- Orchestration requires non-empty `workspace_id`.
- Agent Runtime uses `project_id` = workspace for FileService.
- Specialty `scope_paths` document intended paths; hard path ACL remains FileService/UCIP tool checks.
- Unauthorized empty workspace → error before runtime tools.

---

## 6. Risk & HITL

| Class | Typical effect |
|-------|----------------|
| READ_ONLY | Specialty allow for research/business |
| REVERSIBLE_WRITE | Code/web writes |
| EXECUTION | shell/build — trust OPERATOR+ |
| EXTERNAL_SIDE_EFFECT / IRREVERSIBLE | HITL / deny by specialty |

`HITL_REQUIRED_CAPS` includes delete, outbound network, agent.spawn, etc.  
Mission critical + HITL → `waiting_for_user`, not silent execute.

---

## 7. Trust & XP

- `TRUST_LEVEL_CAPS` caps capabilities by tier.
- **XP never appears in UCIP grant paths** (see `persona_xp` / prior authority tests).
- Level 1 vs 20 does not change UCIP allowlists.

---

## 8. Global denies

`ALWAYS_BLOCKED_CAPS`: e.g. `ucip:system.root`, `ucip:filesystem.format`, `ucip:network.exfiltrate`.  
Aliases map into these (e.g. `db.drop` → format) → same DENY.

---

## 9. Budget

| Layer | Owns |
|-------|------|
| UCIP `BudgetPolicy` | session max iterations / tokens (gateway) |
| AgentRuntime `MAX_STEPS` | tool-loop bound |
| Mission `max_attempts` | repair/retry escalate to ASK_USER |

Not competing auth engines — different scopes. Revisions do not reset UCIP identity budgets automatically.

---

## 10. Error taxonomy (do not collapse)

| Code / state | Meaning |
|--------------|---------|
| Specialty/UCIP DENY | **AUTHORIZATION** |
| `AGENT_RUNTIME_UNAVAILABLE` | **DEPENDENCY / RUNTIME** |
| `MODEL_UNAVAILABLE` | **CREDENTIAL / PROVIDER** |
| Missing workspace | **CONFIGURATION** |
| `waiting_for_user` | **HITL** |

---

## 11. Environment inventory (from code)

### REQUIRED (typical single-machine)

| Variable | Purpose | Class |
|----------|---------|-------|
| `DATABASE_URL` | Persistence | REQUIRED |
| `JWT_SECRET` / `SECRET_KEY` | Auth/crypto | REQUIRED (prod) |
| `DEFAULT_PROVIDER` | LLM selection | REQUIRED for chat/missions |

### Provider (pick one path)

| Variable | Provider | Class |
|----------|----------|-------|
| `OPENROUTER_API_KEY` | OpenRouter | OPTIONAL if using another |
| `OPENROUTER_DEFAULT_MODEL` | Model id | OPTIONAL |
| `OPENAI_API_KEY` | OpenAI | OPTIONAL |
| `GEMINI_API_KEY` | Gemini | OPTIONAL |
| `DEEPSEEK_API_KEY` | DeepSeek | OPTIONAL |
| `OLLAMA_HOST` | Local/remote Ollama | OPTIONAL |
| `TAVILY_API_KEY` | Search | OPTIONAL |

### Mission / test

| Variable | Purpose | Class |
|----------|---------|-------|
| `DEVOS_ORCH_MAX_PARALLEL` | Batch concurrency | OPTIONAL (default 3) |
| `DEVOS_ORCH_FAKE_RUNTIME` | Fake runtime | **TEST ONLY** |
| `DEVOS_ALLOW_FAKE_RUNTIME` | Allow fake | **TEST ONLY** |

### Isolation / sandbox

| Variable | Purpose | Class |
|----------|---------|-------|
| `DEVOS_USE_DOCKER_SANDBOX` | Strong isolation | PRODUCTION recommended |
| `DEVOS_ALLOW_DEGRADED_ISOLATION` | Weak isolation | DEV ONLY |

**Never commit real secrets.**

### Dependency note: `pydantic-settings`

Declared in `requirements.txt` as `pydantic-settings==2.3.0`.  
Sandbox LIVE E2E failure was **incomplete environment install**, not missing declaration.  
Reproducible path: `pip install -r requirements.txt` or Docker image build.

---

## 12. Health

`GET /api/health` → `mission_runtime`:

- `agent_runtime` import
- `fake_runtime_env`
- `orchestration_store`
- `ucip`
- `workspace`
- top-level `providers` list from settings (configured keys present — not a reachability proof)

---

## 13. UCIP smoke (no LLM)

```text
evaluate_node_request(web, {fs.write}) → allow subset
evaluate_node_request(research, {shell.exec}) → deny
ALWAYS_BLOCKED → deny
authorization_decision=deny → runtime not called
```

---

## 14. Known blockers (honest)

| Class | Status |
|-------|--------|
| CODE (prompt format) | Fixed in `3451603` era |
| DEPENDENCY | Full `requirements.txt` / Docker required |
| CONFIGURATION | `.env` from `.env.example` |
| CREDENTIAL | Provider key or working Ollama |
| INFRASTRUCTURE | Ollama host / network |

**Do not weaken UCIP to force missions.** Denied capability → Nuha `ASK_USER` or safer alternative.
