# DEVOS Agentic IDE — Functional Parity Matrix

Baseline commit referenced: `d2ce505` (governed coding-agent foundation).  
This document tracks **capability parity**, not visual clone parity.

**Status values**

| Status | Meaning |
|--------|---------|
| `NOT_IMPLEMENTED` | No meaningful implementation |
| `PARTIAL` | Code exists; incomplete or untested end-to-end |
| `IMPLEMENTED` | Behavior exists in product path |
| `VERIFIED` | Meaningful unit/integration/smoke test passed |

Do **not** mark `VERIFIED` merely because source files exist.

Parity target: a competent developer can perform the same *class* of real software-development tasks without switching environments for a missing capability.

---

## A. Spatial workspace

| Capability | Current implementation | Target | Status | Tests | Known limitation |
|------------|------------------------|--------|--------|-------|------------------|
| Project workspace open | `FileService` under `data/projects/{user}/{project}` | Scoped project root | IMPLEMENTED | PARTIAL (path tests via FileService usage) | No multi-root workspaces |
| File explorer | `FileTree.jsx` (overlay in spatial shell) | Expand, create, rename, delete, refresh | IMPLEMENTED | PARTIAL | Context menu / DnD limited |
| Monaco editor | `DevOS Ide` — ephemeral focus surface, Monaco | Syntax, tabs, dirty, save | IMPLEMENTED | PARTIAL | Large-file protection limited |
| Editor focus layer | Ephemeral surface attached to canvas, not permanent | Spatial context preserved | IMPLEMENTED | — | — |
| Breadcrumbs / minimap | Monaco minimap | Line/col status | PARTIAL | — | — |
| Find/replace in editor | Monaco built-in | Full | IMPLEMENTED | — | — |
| Multi-cursor / folding | Monaco defaults | Full | IMPLEMENTED | — | Not product-tested |
| Workspace search | `/api/search/files`, `SearchPanel` (overlay) | Regex, glob, replace | PARTIAL | — | Regex/replace incomplete |
| Command bar | `CommandBar.jsx` — CMD+K / CTRL+K / SPACE | Central registry + natural-language commands | IMPLEMENTED | — | — |
| Workflow canvas | `OrchestrationCanvas` — real node graph over `/api/scripts` + chains | Node-based orchestration | IMPLEMENTED | — | — |
| AI Copilot | `AICopilot` — contextual, project/node/file-aware | Contextual assistance | IMPLEMENTED | — | — |
| Ghost terminal | `GhostTerminal` — real PyRunner log streaming | Runtime surface | IMPLEMENTED | — | — |
| Agency dashboard | `AgencyDashboard` — live `/api/workers` HUD | Fleet status | IMPLEMENTED | — | — |
| Mobile spatial shell | Same spatial workspace adapted to small viewport | Canvas-first, overlays/sheets | IMPLEMENTED | — | — |

## B. Repository understanding

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| list / read / search files | Agent tools + FileService | Progressive exploration | IMPLEMENTED | VERIFIED (tool schema/mode tests) | Search is substring, not indexed |
| find_symbol / references | Missing / stub | Symbol tools | PARTIAL | — | Heuristic only if added |
| import graph / package deps | Missing | Project metadata tools | PARTIAL | — | Best-effort detectors |
| Git history inspection | git_log/show tools | History + blame | PARTIAL | — | Blame may be missing |
| Full-repo dump to LLM | Avoided by design | Progressive context | IMPLEMENTED | — | Context engine still basic |

## C. Agentic coding

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Multi-step tool loop | `AgentRuntime` MAX_STEPS=24 | Bounded autonomous loop | IMPLEMENTED | PARTIAL | Live LLM E2E not verified in CI |
| Modes Ask/Edit/Agent/Review | Mode allowlists | UX on UCIP | IMPLEMENTED | VERIFIED (mode filter tests) | — |
| Structured patches | apply_patch + conflict | Patch vs concurrent edit | IMPLEMENTED | VERIFIED (patch unit tests) | Unified diff is simple hunks |
| Multi-file edits | Tools + Composer | Coordinated multi-file | PARTIAL | — | Composer still whole-file oriented |
| Stop after one tool | Loop continues until done/max | Full loop | IMPLEMENTED | PARTIAL | — |

## D. Tool execution

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Canonical tool registry | `brain/agent_tools.py` | Schema + risk + capability | IMPLEMENTED | VERIFIED | — |
| UCIP gate per tool | `UCIPGateway.request` in runtime | All consequential tools | IMPLEMENTED | PARTIAL | Full auth matrix tests incomplete |
| run_command / tests / build / lint | Agent tools → subprocess | Governed execution | PARTIAL | — | Heuristic commands; isolation varies |
| Extensible registration | `register_agent_tool` | Trusted config only | IMPLEMENTED | VERIFIED | No untrusted plugin self-grant |

## E. Terminal

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Interactive PTY | `execution/pty_session.py` | Multi-client, scrollback | IMPLEMENTED | PARTIAL (pty tests exist in suite) | Agent uses command tool more than PTY |
| Streaming / ANSI / resize | PTY path | Full | PARTIAL | — | Agent command path is discrete cmds |
| Ctrl+C / terminate | PTY + cancel flags | Agent interrupt | PARTIAL | — | Agent cancel does not always kill OS process |
| Terminal as agent context | Optional terminal_context field | Structured | PARTIAL | — | Not auto-fed from live PTY |

## F. Diagnostics

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Problems panel | LSP diagnostics via `useLSP` hook | Surface errors | PARTIAL | — | Weak LSP coupling |
| Fix from diagnostic | Not first-class | Structured Fix action | NOT_IMPLEMENTED | — | — |
| LSP diagnostics | `execution/lsp_manager.py` + `/api/lsp` + `useLSP` | External server manager | PARTIAL | VERIFIED (path isolation unit tests) | Servers must be installed on host; live hover/definition not CI-verified |

## G. Testing

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| run_tests tool | Heuristic pytest | Multi-ecosystem discovery | PARTIAL | — | Detection incomplete |
| Structured test results | stdout/stderr capture | Failed-test extraction | PARTIAL | — | No unified TestRunner abstraction |
| Fixture E2E agent fix tests | Missing | Controlled repo acceptance | NOT_IMPLEMENTED | — | — |

## H. Git / source control

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| status/diff/stage/commit | GitService + tools + panel | Core SCM | IMPLEMENTED | PARTIAL | — |
| branch/log/show | Tools + service | Core | IMPLEMENTED | PARTIAL | — |
| blame/stash/merge/rebase | Missing or partial | Expanded SCM | NOT_IMPLEMENTED | — | Destructive ops stay gated |
| No auto-push / no hard reset tools | By design | Governed | IMPLEMENTED | VERIFIED (banned tool names test) | git_push exists in Brain contracts, not IDE agent tools |

## I. Context management

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Structured AgentContext | Dataclass in runtime | Progressive | PARTIAL | — | No full context engine |
| @file / @selection refs | Chat mention picker partial | Structured refs | PARTIAL | — | Not all @ types |
| Summarization / compaction | Minimal | Task memory | NOT_IMPLEMENTED | — | Transcript can grow |
| Vector DB | Not used | Deterministic first | N/A | — | Intentional non-goal |

## J–K. Autonomy & long-running tasks

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Bounded loop | MAX_STEPS, budgets | Limits + waiting_for_user | PARTIAL | — | Limits incomplete |
| In-process AgentTask | dict store | Durable DB task | PARTIAL | — | Lost on restart |
| Reconnect / observe | SSE only while open | Persist + reconnect | NOT_IMPLEMENTED | — | — |
| Integrate core/loop.py | Separate systems | Shared governance path | PARTIAL | — | Two loops coexist |

## L–N. Human interaction, review, multi-file

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| HITL / authority escalate | UCIP ESCALATE | Meaningful pauses | PARTIAL | — | IDE agent surfaces requires_authority |
| Change review accept/reject | Snapshots + review API | First-class review | PARTIAL → target IMPLEMENTED | unit tests for revert | Needs product UX verification |
| Composer multi-file | plan/execute/apply | Review diffs | IMPLEMENTED | PARTIAL | Whole-file generation |

## O. Model / provider

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| OpenAI-compatible / Ollama / OpenRouter | BrainLLM | Existing hierarchy | IMPLEMENTED | PARTIAL | Streaming quality varies |
| Agent model selection | provider/model on run | User prefs | IMPLEMENTED | — | — |

## P. Security / governance

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| UCIP on tools | Runtime gate | No bypass | IMPLEMENTED | PARTIAL | Red-team suite incomplete |
| Path isolation | FileService | No traversal | IMPLEMENTED | PARTIAL (existing isolation tests) | Continuous audit needed |
| Prompt injection defense | Scanner + separation | Policy vs data | PARTIAL | — | Not fully proven |
| Secrets not in browser | Design | Preserve | IMPLEMENTED | — | Agent must not freely read .env |

## Q–S. Observability, extensibility, performance

| Capability | Current | Target | Status | Tests | Limitation |
|------------|---------|--------|--------|-------|------------|
| Event stream timeline | agent.* events | UI timeline | PARTIAL | — | No durable timeline UI |
| Evidence correlation | Best-effort | Full | PARTIAL | — | record_evidence API mismatch |
| Tool extensibility | register_agent_tool | Trusted only | IMPLEMENTED | VERIFIED | MCP adapter governed only partially (existing MCP routes) |
| UI non-blocking | SSE async | Responsive | PARTIAL | — | Not load-tested |
| LSP manager | `/api/lsp` + stdio proxy | External servers | PARTIAL | path isolation | DAP still NOT_IMPLEMENTED; rebuild may be needed for frontend |

---

## Parity gates (program checklist)

| Gate | Status |
|------|--------|
| IDE gate | PARTIAL |
| Agent gate | PARTIAL |
| Tool gate | PARTIAL |
| Context gate | PARTIAL |
| Recovery gate | PARTIAL |
| Safety gate | PARTIAL |
| Review gate | PARTIAL |
| Verification gate | NOT_IMPLEMENTED |
| Extensibility gate | PARTIAL |

**Overall functional parity: NOT DECLARED.**

---

## Recommended next stages (in order)

1. Change review snapshots (accept / reject / revert) — this document’s companion work  
2. Repository intelligence tools (metadata, tests, deps, heuristic symbols)  
3. Durable AgentTask + reconnect  
4. Test/build discovery harness  
5. LSP manager foundation  
6. Security red-team expansion  
7. Controlled fixture E2E acceptance  

Each stage must update this matrix honestly.


### Update (durable tasks)

- `AgentTaskRecord` SQLite model
- `brain/agent_task_store.py`
- `GET /api/agent/{id}/events?after_seq=`
- Event `seq` field on agent SSE events
- Recovery gate: PARTIAL
