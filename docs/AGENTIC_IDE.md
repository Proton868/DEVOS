# DEVOS Agentic IDE Architecture

## Overview

The DEVOS editor is a **user interface + orchestration client**. It is not a
second authorization system. All consequential actions flow through existing
governance:

```
User
 ↓
Editor / Agent
 ↓
UCIP capability
 ↓
require_authority()
 ↓
existing execution mechanism
 ↓
Evidence
```

## Architecture

```
                    ┌────────────────────┐
                    │      DEVOS IDE     │
                    │ Monaco / Explorer   │
                    │ Terminal / Agent    │
                    └─────────┬──────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │   Agent Runtime    │
                    │ tool loop / state   │
                    └─────────┬──────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │   Tool Registry    │
                    └─────────┬──────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │ UCIP + Authority   │
                    └─────────┬──────────┘
                              │
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
             Filesystem    Job Queue     Sandbox
                 │            │            │
                 └────────────┼────────────┘
                              ▼
                         Evidence
```

## Components

### Editor (frontend)

- Monaco primary code surface (`CodeEditor.jsx`)
- File explorer (`FileTree.jsx` / `FileTreeWrapper.jsx`)
- Tabs, dirty state, save
- Command palette (`CommandPalette.jsx`)
- Terminal (`SmartTerminal.jsx` + PTY backend)
- Source control (`GitPanel.jsx`)
- Wozzy/Nuha agent panel (`ChatSidebar.jsx` + agent modes)
- Composer multi-file review (`ComposerPanel.jsx`)
- Change review for agent patches

### Agent Runtime (`brain/agent_runtime.py`)

- Iterative tool-call loop driven by LLM
- Modes: Ask / Edit / Agent / Review (UX constraints on tool set)
- Streams structured events (SSE)
- Cancellation propagates to running tools/jobs
- Context is progressive (metadata → search → excerpts → full file)

### Tool Registry (`brain/agent_tools.py`)

Canonical IDE tools with explicit metadata:

| Category   | Tools |
|------------|-------|
| Workspace  | list_files, read_file, search_files, get_file_metadata |
| Editing    | create_file, apply_patch, replace_text, rename_file, delete_file |
| Execution  | run_command, run_tests, run_build, run_linter |
| Git        | git_status, git_diff, git_log, git_show, git_branch, git_add, git_commit |
| Diagnostics| get_job, get_job_logs, get_evidence |
| Workflow   | list_workflows, inspect_workflow, execute_workflow |

Every tool declares: name, description, input_schema, capability,
authority requirement, side_effect, risk, timeout.

**No unrestricted destructive Git tools** (no `git_reset_hard`, no force push).

### Authorization

Tools route through:

1. Schema validation (`ToolValidator` / agent tool schemas)
2. UCIP `ACTION_TO_CAP` + `UCIPGateway.request()`
3. `require_authority()` / PathClass where durable
4. Existing FileService / GitService / sandbox / job mechanisms
5. Evidence recording

Frontend never grants authority. Modes are UX filters only.

### Agent events

```
agent.started | agent.thinking | agent.tool_call | agent.tool_result
agent.file_changed | agent.command_started | agent.command_output
agent.test_started | agent.test_result | agent.error
agent.completed | agent.cancelled
```

Events are scoped to user / tenant / project / task / correlation_id.

### Patch-based editing

Agents prefer structured patches over whole-file rewrites.
Backend validates patch against current file content.
Concurrent modification → patch conflict (no silent overwrite).

### Security invariants

- Path traversal blocked by `FileService._resolve`
- Tenant / project isolation preserved
- Provider credentials never reach the browser
- LLM tool arguments treated as untrusted input
- Agent cannot modify its own authority or UCIP registry
- Untrusted code remains fail-closed sandboxed
- Secrets / `.env` not readable unless explicitly authorized

## API

| Endpoint | Purpose |
|----------|---------|
| `POST /api/agent/run` | Start agent task (SSE stream) |
| `POST /api/agent/{task_id}/cancel` | Cancel running task |
| `GET  /api/agent/{task_id}` | Task status |
| `GET  /api/agent/tools` | List available tools for mode |
| `POST /api/agent/patch/preview` | Preview patch application |
| `POST /api/agent/patch/apply` | Apply accepted patches |

## Non-goals (this phase)

LSP server farm, multi-agent swarm, automatic Git push, automatic production
deploy, speculative vector DB, custom container runtime.

## Durable AgentTask (Recovery)

AgentTask orchestration state is mirrored to SQLite (`agent_tasks` table) via
`brain/agent_task_store.py`. This is **not** ExecutionJob:

| Concern | System |
|---------|--------|
| Script/workflow durable work | `ExecutionJob` |
| IDE coding-agent session state | `AgentTask` / `AgentTaskRecord` |
| Audit trail | `Evidence` |

Events carry a monotonic `seq` per task. Clients may reconnect with:

```
GET /api/agent/{task_id}/events?after_seq=N
```

Events are user-scoped; cross-tenant access is denied.

