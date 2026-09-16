# DevOS Product Vision — Durable Source of Truth

**Status:** Canonical product identity and architectural invariants  
**Audience:** Humans and coding agents implementing DevOS  
**Rule:** Before implementing features, compare the change against this document and `plans/AGENCY_OS_MASTER_ARCHITECTURE.md`. Do not silently reinterpret, simplify, or replace this vision.

---

## 1. Product identity

DevOS is an **AI-native Development Operating System** / **unified development workspace**.

Its purpose is **not** merely “AI app generation.”

The complete product combines, in **one** application/workspace:

| Capability | Role in DevOS |
|------------|----------------|
| AI-assisted application/software building | Intent → plan → specialist work → artifacts |
| Full IDE / code workspace | Edit, navigate, search, refactor |
| Project / files / repository management | Workspace authority, Git, isolation |
| Runtime and terminal execution | Controlled shell, jobs, sandboxes |
| Preview / testing | Run and inspect results |
| Automation / workflow creation | DAG/mission orchestration, workflows |
| Agent creation and orchestration | Personas, specialists, fleet |
| AI-assisted debugging and inspection | Evidence, logs, traces |
| Evidence, logs, and execution history | Provenance and audit |
| Backend / database integration | Project data and SoT stores |
| Deployment | Governed release paths |
| Post-deployment inspection / verification | Truth after ship |

### Core lifecycle (permanent principle)

```text
BUILD → RUN → ORCHESTRATE → INSPECT → DEPLOY
```

### Strategic value

Users should not need separate applications for:

AI app building + IDE + automation + agent orchestration + runtime + deployment.

DevOS should **progressively consolidate** those capabilities into one coherent environment.

---

## 2. Product north star

> **DevOS is the place where humans and AI agents build, run, orchestrate, inspect, improve and deploy software together.**

The user should increasingly be able to stay inside DevOS rather than jumping between separate:

- AI builders  
- IDEs  
- terminals  
- automation platforms  
- agent platforms  
- backend tools  
- deployment tools  
- monitoring / debugging tools  

---

## 3. Nuha’s role

**Nuha is the executive AI / orchestration layer of DevOS.**

Nuha is **not** an unrestricted superuser that personally performs every operation.

### Primary responsibilities

- Understand user intent  
- Reason about the task  
- Create / modify execution plans  
- Select appropriate specialist agents  
- Delegate work  
- Coordinate agents  
- Enforce / trigger governance  
- Inspect evidence / results  
- Request human approval when required  
- Report **truthful** outcomes  
- Learn from **validated** experience  

### Intended control loop

```text
USER
  → NUHA
  → EXECUTION PLAN
  → SPECIALIST AGENTS
  → CONTROLLED CAPABILITIES
  → EXECUTION
  → EVIDENCE / LOGS
  → VERIFICATION
  → NUHA
  → USER
```

Nuha decides **what should happen next**.  
UCIP / governance decides **whether it is authorized**.  
Specialists and runtime **perform** work.  
Verification decides **whether it actually worked**.  
XP / learning records **experience** — never authority.

---

## 4. Internal delegation is the default

**Architectural invariant:** Internal DevOS work should be delegated from Nuha to specialized agents.

Nuha must not bypass the agent / capability / governance architecture simply because she technically could.

Specialist agents should perform appropriate work such as:

- coding  
- web / application construction  
- database work  
- testing  
- research  
- automation  
- deployment  
- inspection  
- debugging  
- infrastructure operations  
- other future specialized capabilities  

Agent delegation must remain **observable and auditable**.

Scaffolding, templates, or direct shortcuts may exist only as **explicit fallbacks**, never as silent “success” paths that claim specialists did the work.

---

## 5. MCP boundary

**MCP is primarily the bridge to external applications/services.**

Internal DevOS capabilities should use the **native** DevOS capability / agent architecture whenever possible.

```text
INTERNAL:
  Nuha → Agent → DevOS Capability → Resource

EXTERNAL:
  Nuha → Agent → MCP → External Application / Service
```

Do **not** turn MCP into a substitute for DevOS’s internal architecture.

---

## 6. Checks and balances (governance)

Governance is a **fundamental product principle**, not optional polish.

Every meaningful agent operation should be capable of association with:

| Field | Meaning |
|-------|---------|
| Requesting user / owner | Who asked |
| Mission / task | What work unit |
| Execution plan | What plan |
| Responsible agent | Who acted |
| Capability used | What power |
| Authorization | UCIP / policy outcome |
| Target resource | What was touched |
| Action | What operation |
| Timestamp | When |
| Result | Outcome claim |
| Evidence | Supporting artifacts / logs |
| Verification status | Truth assessment |
| Approval state | HITL where applicable |

**Principle:** *No invisible consequential work.*

The system should be able to explain what happened.

Governance must remain **observable, testable, and enforceable** — not merely documentation.

---

## 7. Evidence and logging

Logs are **not only** debugging information.

They are part of the AI **governance / provenance** system.

DevOS should progressively be able to answer:

> Who did what, why, under whose authority, using which capability, against what resource, and what was the result?

Execution events, artifacts, evidence, provenance, and verification are **first-class** architectural concepts.

Mission Truth and verification gates must prefer:

> “I don’t know whether this succeeded.”

over false confidence when evidence is insufficient.

---

## 8. Continuous learning / self-development

### Pillar: Continuously improving Nuha + agents

Nuha and specialized agents are intended to become progressively more capable through accumulated experience.

They should be able to learn from:

- previous missions  
- successful implementations  
- failed implementations  
- codebases  
- project architecture  
- execution results  
- errors  
- debugging resolutions  
- tests  
- deployments  
- user preferences where explicitly permitted  
- human corrections  
- agent performance  
- validated solutions  
- accumulated evidence  

Long-term vision: DevOS can continuously improve the capabilities and strategies of Nuha and its agents — including, eventually, **controlled** self-development of agent capabilities and system components.

### Hard boundary

**Continuous learning must not mean uncontrolled self-modification.**

Governed learning lifecycle:

```text
CAPTURE EXPERIENCE
  → EVALUATE
  → CREATE LEARNING CANDIDATE
  → VALIDATE
  → GOVERNANCE CHECK
  → APPROVE / PROMOTE
  → STORE VERSIONED KNOWLEDGE / SKILL
  → REUSE
  → MEASURE OUTCOME
```

Learning and self-improvement must be:

- observable  
- auditable  
- testable  
- versioned  
- reversible  
- governed  
- attributable  
- validated **before** promotion  

The system should preserve the ability to determine **why** an agent became better and **what evidence** justified the improvement.

**XP, levels, and learning never grant security permissions.** UCIP remains the authority boundary.

---

## 9. Human correction is training signal

Human correction is valuable system experience.

When a human corrects code, architecture, workflow, reasoning, tool selection, agent behavior, deployment approach, or implementation strategy, that correction should eventually be capable of becoming **structured learning**, subject to governance and validation.

Goal:

```text
Human correction → validated experience → improved future behavior
```

---

## 10. Project / organizational memory

Plan for multiple layers of accumulated knowledge:

- project knowledge  
- repository knowledge  
- architecture knowledge  
- user-approved preferences  
- agent experience  
- successful solution patterns  
- failure patterns  
- validated skills  
- organizational knowledge  

Avoid treating every task as if the system has no previous experience.

Memory remains subject to tenancy, privacy, and governance constraints.

---

## 11. Self-development boundary

The long-term vision includes the possibility that Nuha and agents can help improve their own capabilities and the systems they operate.

Self-development must remain subject to:

- isolation where appropriate  
- tests  
- evidence  
- governance  
- approvals  
- versioning  
- rollback  
- audit trails  

**An agent must never silently grant itself additional authority** simply because it believes doing so would improve itself.

---

## 12. OmniRoute and model routing

OmniRoute is the **native / default model-routing layer**.

Users and providers remain configurable. The architecture stays **model-agnostic** and must avoid a hard dependency on one LLM vendor.

Nuha should ultimately operate across an appropriate model / provider pool through the provider abstraction:

```text
Nuha / Brain
  → DevOS provider abstraction
  → OmniRoute (default gateway)
  → configured upstream models / providers
```

Ollama and direct provider keys may remain **optional**. Upstream API keys for routed providers should live in OmniRoute (or secure secret stores), not be hard-coded into DevOS frontend or core.

---

## 13. Implementation discipline

Before implementing future features, compare the proposed change against this master vision.

Do **not** allow feature development to gradually turn DevOS into:

- merely another chatbot  
- merely another AI code editor  
- merely another prompt-to-app generator  
- merely another workflow automation platform  
- merely an MCP wrapper  
- an uncontrolled autonomous agent  

Each new capability should strengthen the **unified DevOS architecture**.

When requirements conflict, preserve these priorities:

```text
human control
  + delegation
  + governance
  + evidence
  + verification
  + learning
  + unified workspace
```

### Status vocabulary (mandatory honesty)

Use these labels; do not claim production-ready from unit tests alone:

| Label | Meaning |
|-------|---------|
| **IMPLEMENTED** | Code exists and is integrated in the product path |
| **PARTIALLY IMPLEMENTED** | Present but incomplete, dual-path, or limited scope |
| **TESTED BUT NOT LIVE-PROVEN** | Automated tests pass; production/live proof missing |
| **NOT YET IMPLEMENTED** | Designed or planned only |
| **FUTURE / RESEARCH** | Intentional long-horizon work |

---

## 14. Anti-drift checklist for coding agents

Before merging substantial work, confirm:

1. Does this strengthen BUILD → RUN → ORCHESTRATE → INSPECT → DEPLOY inside one workspace?  
2. Does Nuha **delegate** internal work to specialists rather than silently doing everything?  
3. Does consequential work leave **evidence** and pass **governance**?  
4. Does success require **verification**, not only “no exception”?  
5. Does learning/XP remain non-authoritative?  
6. Is MCP used for **external** bridges, not as a replacement for internal capabilities?  
7. Is the status label honest (especially live vs unit-tested)?  

If the answer to any of 1–6 is no, stop and realign to this document.

---

*End of durable product vision. Companion technical organ map and historical roadmap: `plans/AGENCY_OS_MASTER_ARCHITECTURE.md`.*
