# DEVOS Cognitive Architecture

**Intelligence decides. Workflows orchestrate. Runtime executes. Governance controls. Evidence proves. Durable state remembers.**

## Layers

| Layer | Module | Role |
|-------|--------|------|
| Strategic HAI | `cognitive/hai_control.py` | Plan, subgoals, replan, complete, block |
| Tactical HAI | same (decisions only) | Tool suitability, recovery, verify escalate |
| GoalDecomposer | `cognitive/decomposer.py` | LLM DAG decomposition (available infrastructure) |
| Coordinator | `cognitive/coordinator.py` | Deterministic multi-worker orchestration |
| AgentRuntime | `brain/agent_runtime.py` | Single governed tool loop |
| WorkerRuntime | `workers/runtime.py` | Delegated worker execution |
| Workflows | `brain/workflow_*.py` | Deterministic orchestration |
| ExecutionJob | durable | Authoritative execution truth |
| HAI checkpoint | `cognitive/hai_checkpoint.py` | Durable cognitive state on AgentTask |
| UCIP / authority | `governance/` | Capability and policy |

## Agent mode control flow

```
AgentTask
  → StrategicController.start (plan + subgoal)
  → AgentRuntime tool loop (BrainLLM + tools)
  → UCIP / authority / execution
  → HAI on_tool_result (verify / continue / replan / block / complete)
  → checkpoint at durable boundaries
```

Strategic and Tactical **never** execute tools, shell, or grant authority.

## Budget exhaustion

Reaching `MAX_STEPS` is **blocked/incomplete**, not success.

## Recovery

See `docs/HAI_ARCHITECTURE.md` — ExecutionJob always wins over HAI assumptions.
