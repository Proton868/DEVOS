# Agency OS — Master Architecture & Build Plan
*Grounded in the actual current state of every source repo, not assumptions.*

---

## 0. Why this plan differs from the uploaded document

I pulled the live README/spec/source of all seven repos before writing anything. Six of the seven behave nothing like the doc described. This matters because it changes what's actually reusable vs. what you have to build from scratch. Here's the corrected picture, repo by repo:

| Repo | Doc claimed | Actually is | Impact |
|---|---|---|---|
| **UCI-Universal-Capability-Interface** (yours) | Has "Intent Identity" as a distinct system | Real spec exists: `IntentRequest → ExecutionPlan → Capability Execution → EvidenceChain(DAG) → Outcome`, with signed capability manifests, federation, and `/api/uci/replay`. But **identity is just an `actor` field on IntentRequest** — there's no separate identity/trust-tier object yet. Explicitly **not** a reasoning/planning engine — it's a governance layer that sits *above* agent runtimes. | You'll need to design and add an `IdentityContext` schema (trust tier, capability tokens, requester profile) — this doesn't exist in v0.1. Good news: your existing UCIP governance layer in DevOS already has 5-tier trust + capability tokens, so you're ahead of the public repo, not behind it. |
| **PyRunner** | "Execution Engine" with sandboxing, notebook execution, isolated environments | A Django app that schedules and runs Python scripts with per-script venvs, encrypted secrets, run logs, and email/webhook/Telegram alerts. Single container, SQLite, MIT license. | Great as the **Scheduled Jobs organ** (cron-style automations). Not a sandboxed multi-language execution engine — you already have something closer to that in DevOS's own SandboxedExecutor. |
| **odysseus** (pewdiepie-archdaemon) | "Cognitive Engine" for reasoning/planning/memory/navigation | A full self-hosted AI workspace: chat+agents+MCP+tools, Deep Research, a writing editor, an IMAP/SMTP email client, notes/tasks/CalDAV calendar, image tools. 79.7k stars, actively developed. **License: AGPL-3.0-or-later.** | This is the single biggest correction. AGPL is copyleft — if you fork its code into a service you operate for tenants, you likely trigger source-disclosure obligations for that service. For a commercial multi-tenant SaaS like Admin ESA, **don't vendor its code directly**. Two safe paths: (a) run it as a separate, clearly-licensed self-hosted service that Agency OS talks to only over HTTP/MCP (network interaction, no code linking — the standard safe pattern), or (b) study its Deep Research / email / calendar feature *architecture* for inspiration and build your own implementation clean-room. I'd recommend (a) for anything you want fast, (b) for anything that touches tenant data directly. **Get this in front of a lawyer before shipping**, not after — I'm not one. |
| **autoresearch** (karpathy) | "Research System" — literature review, citations, knowledge graphs | An ML training-loop agent that autonomously edits a GPT-training codebase, trains for a fixed 5-minute budget on a **single NVIDIA GPU**, scores against one metric, keeps or discards the change, and repeats overnight. Nothing to do with web research or citations. | Cannot run on your Acer Aspire One or Oracle Free Tier target (needs an NVIDIA GPU). The transferable *idea* — propose → test → score → keep/discard → repeat — is exactly the shape of a code-improvement ratchet loop, and maps far better onto your **Learning Engine / Ponytail pipeline** than onto "Research." Build the actual Research System (web search, source reading, synthesis, citations) as new work. |
| **AIS-OS** (nateherkai) | "Operating System Core" — runtime, sessions, jobs, lifecycle | A markdown-only Claude Code starter kit: a `/onboard` interview that fills out context files, plus `/audit` and `/level-up` weekly review skills. No code, no runtime, no session/job/lifecycle logic at all. | Not usable as OS-core material — there's nothing to port. The one genuinely useful pattern: its 7-question `/onboard` interview that auto-populates a business-context file is a nice UX model for **tenant onboarding** in Admin ESA / Agency OS setup wizards. That's the whole contribution. |
| **agency-agents** (msitarzewski) | "Worker Civilization," hundreds of agent professions | Real and substantial: **300+ markdown persona/system-prompt files** across 16 divisions (engineering, design, marketing, sales, finance, security, testing, GIS, game dev, academic, and more), MIT licensed, with install scripts for Claude Code, Cursor, Codex, Gemini, and others. | This is the best-matching repo in the whole list — it already exceeds your "300+ workers" target. But each file is a **system prompt**, not a running agent — no memory, no tool execution, no UCIP wiring. The work is building a thin runtime wrapper (a `CapabilityDescriptor` + model call + tool access) around each persona. |
| **ponytail** (DietrichGebert) | A pipeline: Architect → Coder → Reviewer → Simplifier → Tester → Red Team → Deploy → Learn | A minimalism *ruleset*: "think like the laziest senior dev in the room — the best code is the code you never wrote." Installs as always-on injected context + lifecycle hooks + slash commands into coding agents. Enforces one thing above all: every non-trivial change ships with exactly one runnable self-check, and safety-critical code (validation, auth, error handling) is never cut for brevity. MIT. | There is no pipeline in the repo to copy. Your instruction to make Ponytail "the senior expert that finalizes and simplifies before the tester" is your own design decision, and a good one. Build it as a pipeline you define, using ponytail's actual mechanism (rule injection via hooks before each agent turn + a mandatory self-check artifact) as the concrete pattern for the "Simplifier" stage. |

**Net effect on your plan:** the architecture below is still yours — standalone, modular, UCIP-governed, Supabase-backed, multi-tenant, Acer-compatible, LLM-agnostic, hundreds of workers, Ponytail-style finalization loop. What changes is *where the real leverage is*: `agency-agents` gives you the worker roster almost for free; `PyRunner` gives you scheduled jobs almost for free; everything else (Brain, Memory, Research, the actual pipeline orchestration, Identity) is work you do yourself, informed by these repos rather than merged from them.

---

## 1. Revised Organ Map (v2)

```
                              Admin ESA (tenant-facing SaaS)
                                         │
                             REST │ MCP │ Events │ UCIP
                                         │
                             ┌────────────────────────┐
                             │        Agency OS         │
                             │   (standalone AIOS)      │
                             └────────────────────────┘
                                         │
   ┌───────────┬───────────┬───────────┼───────────┬────────────────┬───────────────┐
Cognitive     Memory      Workers   Communications  Runtime       Governance
 System     (subdivided) (agency-    (nervous       (PyRunner-    (owns capabilities;
 (was       internally   agents +    system —       style jobs +   UCIP is the
 "Brain")   below        runtime     event bus,     sandboxed      protocol surface
                          wrapper)   pub/sub, MCP/   multi-lang     over 6 engines
                                     REST/WS         exec)          below)
                                     transport,
                                     queues)
```

**Governance, expanded** (capabilities are governance objects; Workers consume them, Cognitive System discovers them, Runtime executes them, Governance owns them):
```
Governance
├── Identity            — actor, tenant, trust tier, delegation chain
├── Capability Registry — the manifest of every capability, versioned, signed
├── Policy Engine        — what's allowed, given identity + context (prompt-injection
│                          scanning + approval gates live here)
├── Trust Engine          — evaluates trust tier against requested action (AuthZ,
│                          separate from Identity's AuthN role)
├── Audit                — who did what, when, under whose delegation
├── Evidence              — the EvidenceChain DAG UCIP already produces
└── UCIP                  — not a peer of the above six, it's the protocol/interface
                            that exposes all six uniformly to the rest of the system
                            and to external callers (REST/MCP)
```

**Memory, expanded** (internal only — tenants and users never see this division; on the Micro Acer profile these are logical schemas/namespaces in one SQLite/Supabase backend, not separate services):
```
Memory
├── Episodic    — what happened, in order (conversation/session history)
├── Semantic    — facts, entities, relationships (knowledge graph)
├── Vector      — pgvector embeddings for retrieval
├── Working     — active task/session scratch space, ephemeral
├── Long-term   — consolidated knowledge that survived the Learning loop
├── Tenant      — per-tenant isolated context (multi-tenant boundary lives here)
└── Learning    — lessons from the Coding/Learning ratchet loop (§ Stage 4)
```

- **Cognitive System** (renamed from Brain — it's an architecture, not a component): planner, reflector, coordinator, scheduler, critic, goal manager, decomposer, negotiator. Nothing off-the-shelf fits this; build UCIP-native from day one.
- **Communications** (new organ): every other organ talks through this, not directly to each other. Owns the event bus, pub/sub, MCP transport, REST transport, WebSockets, queue, notifications, internal messaging, and distributed execution coordination. This closes a real gap in v1.
- **Workers**: `agency-agents` personas + a runtime wrapper that turns each `.md` persona into a UCIP `CapabilityDescriptor` registered in the Capability Registry.
- **Runtime**: PyRunner's scheduling/secrets/venv model for cron-style jobs, plus your own sandboxed executor for on-demand multi-language execution. Runtime *executes* capabilities; it never defines or owns them — that's Governance's job.
- **Research**: net-new, informed by Odysseus's Deep Research feature *concept* but implemented independently.

---

## 2. UCIP Extension: Identity Context (net-new work)

Current UCIP v0.1 `IntentRequest` only carries `actor` as a hint. To get real intent-identity control, add:

```json
IdentityContext {
  actor_id: string,
  tenant_id: string,
  trust_tier: enum[public, tenant_user, tenant_admin, agency_operator, system],
  capability_tokens: [ CapabilityToken ],
  expected_outcome_schema: JSONSchema,
  delegation_chain: [ actor_id ]
}
```

Wire this into the execution model as:
`IntentRequest(+IdentityContext) → ExecutionPlan (worker/capability selection gated by trust_tier) → Capability Execution → EvidenceChain → Outcome (validated against expected_outcome_schema)`

---

## 3. LLM Router (provider-agnostic, as requested)

Single interface, swap by config only:

```
LLM Router
├── OpenAI-compatible (generic adapter)
├── Ollama (ollama.carai.agency — your existing instance)
├── OpenRouter (free-tier models)
├── HuggingFace Inference (free-tier models)
├── DeepSeek (native or OpenAI-compatible endpoint)
├── Anthropic (native, for capability-heavy workers)
└── Nararouter (https://router.bynara.id/v1)
```

Swap knobs: provider, model, temperature, budget ceiling, latency target, context window. Fallback chain: if a free-tier provider rate-limits, router demotes to next provider in priority list automatically.

---

## 4. Acer Aspire One Runtime Profiles

| Profile | RAM | What runs |
|---|---|---|
| Micro | 1–2 GB | UCIP kernel + Memory client only; all LLM calls routed to `ollama.carai.agency` or free-tier cloud; no local inference |
| Standard | 4–8 GB | Above + local embeddings model, a handful of concurrent workers, lightweight workflows |
| Enterprise | 16 GB+ / GPU | Full worker civilization, local model hosting, Research + Learning engines active |

Every organ above Micro is optional and loads on demand via the event bus.

---

## 5. Phased Roadmap

### Stage 1 — Foundation
- **Before anything else: resolve the Odysseus-copy provenance question.** Check the license history (commit log on the LICENSE file) of the Odysseus repo you copied from DevOS. If AGPL was added *after* your copy date, whatever license applied at copy time likely governs your snapshot. If AGPL was already in effect, that copied code carries the obligation regardless of upstream changes since.
- Audit DevOS Platform + DevOS v3 for what's reusable as-is vs. what needs rebuilding to fit the v2 organ map (§1)
- Formalize `IdentityContext` in your UCIP node (§2), as part of Governance's Identity engine
- Stand up **Communications** early, not late — every other organ depends on it existing first: event bus, pub/sub, MCP/REST/WS transport, queue
- LLM Router with the 7 providers above + fallback chain
- Supabase schema baseline: tenants, users, memberships (reuse your Admin ESA target schema), with Memory's 7-way internal division (§1) applied as schema namespaces from the start
- Docker packaging with Micro/Standard/Enterprise profiles
- **One app, not merged parts:** DevOS Platform and DevOS v3 get folded into a single Agency OS codebase — components get absorbed into the v2 organ structure (§1), not run side-by-side as separate services.

### Stage 2 — Cognitive System (formerly "Brain")
- Intent parsing → goal decomposition → task planning, built UCIP-native
- Reflection/self-critique (critic role) loop, logged as EvidenceChain steps
- Coordinator + scheduler + negotiator roles for multi-worker delegation
- Knowledge graph in pgvector (Memory: Semantic)

### Stage 3 — Workers (agency-agents integration — your fastest win)
- Write the persona→CapabilityDescriptor converter (parses each `.md` file's frontmatter/sections into a UCIP-compliant capability manifest)
- Worker registry in Supabase, keyed to the 16 divisions already in the repo
- Delegation + team-formation logic using `delegation_chain`

### Stage 4 — Coding & Learning Loop (your Ponytail design, built from scratch)
- Pipeline: Architect → Planner → Engineer → **Simplifier (Ponytail-pattern: rule-injection + mandatory one-check)** → Reviewer → Security → Tester → Chaos Test → Fix → Retest → Deploy → Learn
- Ratchet-loop scorer (autoresearch-inspired: propose → test → score → keep/discard → log lesson to Learning table)
- PyRunner-style scheduler for nightly self-evolution runs, gated by human approval before deploy

### Stage 5 — Research (net-new, Odysseus-inspired architecture only)
- Web research, source reading, citation tracking, report generation — clean-room implementation
- If speed matters more than IP cleanliness short-term, stand up Odysseus as a **separate AGPL-licensed service** you call over MCP — flag for legal review either way

### Stage 6 — Workflow Engine
- Visual + YAML + JSON + natural-language workflow authoring, all compiling to the same UCIP ExecutionPlan format

### Stage 7 — Enterprise Features
- RBAC keyed off `trust_tier`, audit logs from EvidenceChain, secrets management (PyRunner's Fernet-based pattern is a fine reference), billing hooks, capability marketplace

### Stage 8 — AI Operating System packaging
- Tauri desktop app, headless server mode, CLI, web UI, Admin ESA integration, self-hosted installer, offline-first Micro profile

### Stage 9/10 — Evolution (deliberately last, not folded into Learning)
- Performance Analysis → Pattern Detection → Workflow Optimization → Capability Refactoring → Worker Promotion → Prompt Evolution → Knowledge Compression
- **Approval policy: human sign-off required for every category by default.** Low-risk categories only (e.g. prompt tweaks) may expose a **per-tenant, per-category toggle** once the Trust Engine has accumulated sufficient confidence history. Every Evolution proposal is logged in full regardless of approval path.