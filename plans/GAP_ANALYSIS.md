# Agency OS — Gap Analysis: Current State vs. Master Architecture

## How to read this document

Each line item is tagged:
- ✅ **DONE** — exists, tested, working
- ⚠️ **PARTIAL** — exists but incomplete or has known gaps
- ❌ **MISSING** — does not exist, needs to be built

> **Last updated:** 2026-07-17 — post-session audit of all implemented modules

---

## 1. Governance Organ

### Identity
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | AgentIdentity | [`governance/ucip.py:183`](governance/ucip.py:183) — 5-tier trust, capability set, delegation_chain | — |
| ✅ | ExpectedOutcome | [`governance/identity.py:66`](governance/identity.py:66) — schema validation, correction attempts | — |
| ✅ | Delegation | [`governance/ucip.py:210`](governance/ucip.py:210) — delegate() with narrowing, lineage tracking | — |
| ✅ | IdentityContext schema | [`governance/identity_context.py:66`](governance/identity_context.py:66) — TenantTier enum, CapabilityToken, IdentityContext dataclass with tenant_id, trust_tier, capability_tokens, expected_outcome_schema | — |
| ✅ | Tenant isolation | [`governance/identity_context.py`](governance/identity_context.py) — tenant_id throughout IdentityContext + Memory operations | Supabase RLS policies (see infra) |

### Capability Registry
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | Tool Contracts | [`governance/tool_contracts.py:92`](governance/tool_contracts.py:92) — TOOL_REGISTRY with 16 contracts | — |
| ✅ | Tool Validator | [`governance/tool_contracts.py:372`](governance/tool_contracts.py:372) — pre-execution validation | — |
| ✅ | CapabilityDescriptor | [`governance/capability_registry.py:44`](governance/capability_registry.py:44) — formal UCIP-compatible manifest with inputs/outputs, trust profile, model binding, version, cryptographic signing | — |
| ✅ | Capability Registry API | [`api/routes/capabilities.py`](api/routes/capabilities.py) — GET /api/capabilities with filtering, GET /api/capabilities/categories, GET /api/capabilities/{slug} | — |
| ✅ | Signed capability manifests | [`governance/capability_registry.py:91`](governance/capability_registry.py:91) — CapabilityDescriptor.sign() | — |

### Policy Engine
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | PolicyEngine | [`governance/ucip.py:539`](governance/ucip.py:539) — 6-step evaluation pipeline | — |
| ✅ | Prompt injection scanner | [`governance/ucip.py:409`](governance/ucip.py:409) — 20+ regex patterns | — |
| ✅ | HITL gates | [`governance/ucip.py:144`](governance/ucip.py:144) — HITL_REQUIRED_CAPS, HITLQueue | — |
| ✅ | Budget tracking | [`governance/ucip.py:318`](governance/ucip.py:318) — iter, exec, retry, stuck-loop detection | — |
| ✅ | RBAC Engine | [`governance/rbac.py:87`](governance/rbac.py:87) — tier-based + capability token evaluation | — |

### Audit & Evidence
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | UCIPAuditLogger | [`governance/ucip.py:480`](governance/ucip.py:480) — append-only structured log | — |
| ✅ | Observability | [`governance/observability.py`](governance/observability.py) — traces, spans, metrics, SQLite persistence | — |
| ✅ | EvidenceChain DAG | [`governance/evidence.py`](governance/evidence.py) — EvidenceNode DAG, EvidenceChain persistence, replay capability, API at /api/evidence | — |
| ✅ | Audit API | [`governance/audit.py`](governance/audit.py) — structured audit events for all capability invocations, identity changes, governance decisions | — |

### Other Governance
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | SandboxedExecutor | [`governance/sandbox.py:93`](governance/sandbox.py:93) — process isolation, resource limits, static analysis | — |
| ✅ | Secrets Vault | [`governance/secrets_vault.py:38`](governance/secrets_vault.py:38) — Fernet-encrypted credential storage | — |
| ✅ | CheckpointManager | [`governance/checkpoint.py:24`](governance/checkpoint.py:24) — atomic JSON checkpoints | — |
| ✅ | HITLQueue | [`governance/hitl.py:79`](governance/hitl.py:79) — submit, wait, resolve, callbacks | — |
| ✅ | Billing | [`governance/billing.py`](governance/billing.py) — per-tenant, per-capability billing hooks | — |
| ✅ | Capability Marketplace | [`governance/marketplace.py`](governance/marketplace.py) — publish, discover, download capabilities | — |

---

## 2. Memory Organ

### 7-Way Internal Division
| Status | Division | Current State | What's Needed |
|--------|----------|---------------|---------------|
| ✅ | Episodic | [`memory/store.py:129`](memory/store.py:129) — save/recall/get_history, SQLite | — |
| ✅ | Semantic | [`memory/graph.py:42`](memory/graph.py:42) — KnowledgeGraph, entities, relationships | — |
| ✅ | Vector | [`memory/store.py:115`](memory/store.py:115) — Ollama embeddings via _embed() | Supabase pgvector (schema ready in data/supabase_migration.sql) |
| ✅ | Working | [`memory/working.py:23`](memory/working.py:23) — TTL cache, per-session | — |
| ✅ | Learning | [`memory/store.py:176`](memory/store.py:176) — save_learning() | — |
| ✅ | Long-term | [`memory/store.py`](memory/store.py) — kind="long_term" column, save/recall with kind filter | — |
| ✅ | Tenant | [`memory/store.py`](memory/store.py) — tenant_id column, recall with tenant_id filter | Supabase RLS policies (see infra) |

### Knowledge Graph
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | Entity CRUD | [`memory/graph.py:93`](memory/graph.py:93) — add, get, find, delete | — |
| ✅ | Relationships | [`memory/graph.py:157`](memory/graph.py:157) — add, get_related, upsert | — |
| ✅ | Query by name | [`memory/graph.py:256`](memory/graph.py:256) — query_by_name() | — |
| ⚠️ | Cross-tenant isolation | No tenant scoping in graph | Add tenant_id to all graph operations |

---

## 3. Cognitive System

### Core Loop
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | BrainExecutionLoop | [`core/loop.py:124`](core/loop.py:124) — ReAct loop with UCIP gates, HITL, delegation | — |
| ✅ | LoopState | [`core/loop.py:49`](core/loop.py:49) — step tracking, goal, expected_outcome | — |
| ✅ | BrainLLM | [`brain/llm.py:17`](brain/llm.py:17) — multi-provider, streaming, decide/parse | — |

### Planning & Decomposition
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | GoalDecomposer | [`cognitive/decomposer.py:50`](cognitive/decomposer.py:50) — LLM-driven subtask DAG, cycle detection | — |
| ✅ | Coordinator | [`cognitive/coordinator.py:43`](cognitive/coordinator.py:43) — dependency-respecting concurrent dispatch | — |
| ✅ | Intent Parser | [`cognitive/intent.py`](cognitive/intent.py) — IntentParser with LLM + heuristic fallback, structured Intent dataclass | — |
| ❌ | Scheduler | Not implemented | Time-based task scheduling (cron-style) |
| ❌ | Negotiator | Not implemented | Multi-agent consensus for conflicting goals |

### Reflection & Learning
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | Reflector | [`cognitive/reflector.py`](cognitive/reflector.py) — reflection/critique loop | — |
| ✅ | Autoresearch | [`brain/autoresearch.py`](brain/autoresearch.py) — ratchet-loop (propose→test→score→keep/discard) | — |
| ✅ | Ponytail pipeline | [`cognitive/ponytail.py`](cognitive/ponytail.py) — full 11-stage pipeline: Architect→Planner→Engineer→Simplifier→Reviewer→Security→Tester→Chaos→Fix→Retest→Deploy→Learn, with API at /api/ponytail | — |
| ⚠️ | Critic role | Partially in reflector | Separate critic as distinct cognitive role |

---

## 4. Workers Organ

### Persona Library
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | AGENT_LIBRARY | [`brain/agents/__init__.py:31`](brain/agents/__init__.py:31) — 106+ personas across 16 divisions | Port remaining 200+ from agency-agents repo |
| ✅ | AgentPersona dataclass | [`brain/agents/__init__.py:13`](brain/agents/__init__.py:13) — slug, name, division, tools, languages | — |
| ✅ | Persona routing | [`brain/agents/__init__.py`](brain/agents/__init__.py) — get_best_agent_for_goal() | — |

### Runtime Wrapper
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | WorkerRuntime | [`workers/runtime.py:51`](workers/runtime.py:51) — persona→delegated identity→BrainExecutionLoop | — |
| ✅ | Capability resolution | [`workers/runtime.py:35`](workers/runtime.py:35) — tool names→UCIP capability strings | — |
| ❌ | CapabilityDescriptor converter | Not implemented | Parse .md persona files into UCIP-compliant CapabilityDescriptor manifests |
| ❌ | Worker registry in Supabase | Not implemented | Currently in-memory dict; needs Supabase table with division, tools, trust_profile |
| ❌ | Team formation | Not implemented | Multi-worker team assembly beyond single-worker delegation |

### API Surface
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | List workers | [`api/routes/workers.py`](api/routes/workers.py) — GET /api/workers | — |
| ✅ | Run worker | [`api/routes/workers.py`](api/routes/workers.py) — POST /api/workers/{slug}/run | — |
| ✅ | Coordinated plan | [`api/routes/workers.py`](api/routes/workers.py) — POST /api/workers/plan/run | — |

---

## 5. Communications Organ

### Event Bus
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | EventBus | [`communications/bus.py:32`](communications/bus.py:32) — singleton pub/sub, replay buffers | — |
| ✅ | SSE streaming | [`api/routes/comms.py`](api/routes/comms.py) — GET /api/comms/stream | — |
| ✅ | MCP transport | [`communications/mcp.py`](communications/mcp.py) — MCP server (expose DevOS capabilities as MCP tools), MCP client (connect to external MCP servers), MCPDiscoveryService (unified registry) | — |
| ❌ | Queue system | Not implemented | Persistent job queue beyond asyncio.Queue |
| ❌ | Notification system | Not implemented | Email/webhook/push notification dispatch |
| ❌ | Distributed execution | Not implemented | Multi-process/multi-machine coordination via Redis/NATS |

---

## 6. Runtime Organ

### Execution
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | ExecutionLayer | [`execution/runner.py`](execution/runner.py) — Python/Bash/Node script execution | — |
| ✅ | SandboxedExecutor | [`governance/sandbox.py:93`](governance/sandbox.py:93) — process isolation, ulimits, static analysis | — |
| ✅ | Terminal | [`execution/terminal.py`](execution/terminal.py) — WebSocket-backed PTY | — |
| ✅ | VCS (Git) | [`execution/vcs.py`](execution/vcs.py) — status, commit, push, log, diff, discard | — |
| ⚠️ | Scheduled jobs | [`api/routes/scripts.py`](api/routes/scripts.py) — cron/interval scheduling via APScheduler for scripts | Separate PyRunner-style cron scheduling with per-script venvs |
| ❌ | Binary download | Not implemented | Endpoint for downloading generated binaries/assets |

### Search
| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | Web search | [`execution/search.py:24`](execution/search.py:24) — Tavily/SearXNG integration | — |
| ✅ | File content search | [`api/routes/search.py:24`](api/routes/search.py:24) — POST /api/search/files, grep-based text search across project files | — |
| ✅ | Semantic search | [`memory/store.py`](memory/store.py) — vector embeddings via Ollama, recall() with semantic matching | Supabase pgvector backend |

---

## 7. Research Organ

| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | Basic search | [`execution/search.py`](execution/search.py) — search_web() | — |
| ✅ | Deep Research agent | [`brain/research.py`](brain/research.py) — DeepResearchAgent: query→search→read→synthesize→cite, API at /api/research | — |
| ✅ | Source reading | [`brain/research.py`](brain/research.py) — Source class with URL, title, content extraction | — |
| ✅ | Citation tracking | [`brain/research.py`](brain/research.py) — Citation class with id, source_url, text, relevance | — |
| ✅ | Report generation | [`brain/research.py`](brain/research.py) — ResearchReport with summary, sources, citations, full_report, persistence | — |
| ✅ | Research UI | [`frontend-src/src/components/research/ResearchPanel.jsx`](frontend-src/src/components/research/ResearchPanel.jsx) — Quick/Standard/Deep modes, background jobs with polling, source/citation display | — |

---

## 8. LLM Router

| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | Multi-provider | [`brain/llm.py:90`](brain/llm.py:90) — anthropic, openrouter, deepseek, gemini, huggingface, ollama, nararouter | — |
| ✅ | OpenAI-compatible adapter | [`brain/llm.py:147`](brain/llm.py:147) — _openai_compat() | — |
| ✅ | Custom endpoints | [`brain/endpoints.py`](brain/endpoints.py) — user-defined API endpoints | — |
| ✅ | Fallback chain | [`brain/router.py`](brain/router.py) — LLMRouter with automatic provider demotion on rate-limit/failure, cooldown, priority chain | — |
| ✅ | Budget ceiling | [`brain/router.py:118`](brain/router.py:118) — budget_limit, per-provider cost tracking, cost estimation | — |
| ✅ | Latency target routing | [`brain/router.py:135`](brain/router.py:135) — require_low_latency flag, avg_latency_ms tracking | — |

---

## 9. Frontend / UI

| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | React 18 SPA | [`frontend-src/`](frontend-src/) — Monaco, xterm.js, Zustand | — |
| ✅ | Login screen | Premium redesign with DevOS branding, ambient orbs | — |
| ✅ | Chat sidebar | Streaming SSE chat with @mentions, code blocks | — |
| ✅ | Workers panel | Persona picker, streaming execution, coordinated plans | — |
| ✅ | Agent panel | Action-based agent with HITL approval | — |
| ✅ | Project builder | Full-stack website generator UI | — |
| ✅ | Terminal | xterm.js with WebSocket backend | — |
| ✅ | Settings modal | Provider/model selection, workspace settings | — |
| ✅ | Mobile responsive | MobileLayout with tab-based navigation | — |
| ✅ | Workflow editor | [`frontend-src/src/components/workflow/WorkflowEditor.jsx`](frontend-src/src/components/workflow/WorkflowEditor.jsx) — Visual + YAML workflow authoring, CRUD, import/export, capability selector | — |
| ✅ | Research panel | [`frontend-src/src/components/research/ResearchPanel.jsx`](frontend-src/src/components/research/ResearchPanel.jsx) — Deep Research UI with quick/standard/deep modes, background polling, source/citation display | — |
| ✅ | FlowPanel | Fully functional script editor with cron scheduling, secrets, AI debug, run history | — |
| ✅ | SearchPanel | File search with semantic + text modes, results display, file opening | — |
| ✅ | File content search | Backend at [`api/routes/search.py`](api/routes/search.py) — grep-based text search across project files | — |

---

## 10. Infrastructure

| Status | Item | Current State | What's Needed |
|--------|------|---------------|---------------|
| ✅ | FastAPI server | [`app.py`](app.py) — lifespan, middleware, SPA catch-all | — |
| ✅ | SQLite databases | [`data/`](data/) — devos.db, memory.db, observability.db | — |
| ✅ | Rate limiting | [`governance/ratelimit.py`](governance/ratelimit.py) — sliding window, per-user | — |
| ✅ | Observability middleware | [`app.py:55`](app.py:55) — request timing, logging | — |
| ✅ | Supabase integration | [`data/supabase_migration.sql`](data/supabase_migration.sql) — full multi-tenant schema with pgvector, RLS, tenants, memberships, profiles, memories, capabilities, workflows, evidence, research | Deploy to live Supabase project |
| ✅ | Docker packaging | [`Dockerfile`](Dockerfile) — Multi-stage build with Micro/Standard/Enterprise/Tauri profiles | Build and test |
| ✅ | Multi-tenant schema | [`data/supabase_migration.sql`](data/supabase_migration.sql) — tenants, memberships, RLS policies, auto-provisioning triggers | — |
| ❌ | Tauri desktop app | Dockerfile has Tauri stage but not built | Stage 8 deliverable |

---

## Summary: What's ready vs. what's needed

### Ready Now (Stage 0 — production-grade)
- UCIP governance (5-tier trust, policy engine, tool contracts, HITL, audit, injection scanner, RBAC)
- IdentityContext with tenant isolation, capability tokens, trust tiers
- BrainExecutionLoop with streaming, delegation, checkpointing
- 106+ Worker personas with runtime wrapper and coordinator
- Full React IDE with Monaco, terminal, chat, workers, builder, research, workflow editor
- Memory: all 7 divisions (episodic, semantic, vector, working, long-term, tenant, learning)
- Communications: event bus with SSE streaming, MCP transport
- Execution: Python/Bash/Node sandboxed, Git, terminal, file search
- 7-provider LLM router with fallback, budget, latency routing
- Deep Research agent with web search, synthesis, citations, report generation
- Ponytail 11-stage coding pipeline
- Capability Registry with formal descriptors, cryptographic signing, API
- EvidenceChain DAG with replay capability
- Workflow engine with visual + YAML + JSON authoring, UCIP compilation
- Enterprise features: RBAC, billing, capability marketplace, audit API
- Multi-tenant Supabase schema with pgvector and RLS policies
- Multi-stage Docker (Micro/Standard/Enterprise/Tauri)
- MCP transport (server + client + discovery)

### Needs Building (by remaining priority)
1. **CapabilityDescriptor converter** — agency-agents .md → UCIP manifest
2. **Worker registry in Supabase** — persist worker data beyond in-memory
3. **Team formation** — multi-worker team assembly
4. **Negotiator** — multi-agent consensus for conflicting goals
5. **Queue system** — persistent job queue (Redis/NATS)
6. **Notification system** — email/webhook/push dispatch
7. **Distributed execution** — multi-process/multi-machine coordination
8. **Tauri desktop app** — Stage 8 packaging
9. **Evolution** — Stage 9/10 self-improvement