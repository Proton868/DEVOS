# Agentic SSH governance

```
IntentRequest → ExecutionPlan (planner)
             → SSH.Exec capability request
             → command policy (risk class)
             → host ownership + host-key verify
             → credential resolve (server-side only)
             → transport exec
             → evidence → Nuha (sanitized)
```

## Separation

| Role | May do | Must not |
|------|--------|----------|
| Nuha / model | Propose plan & commands | Hold keys, open raw transport, lower risk class |
| Policy engine | Classify risk, require confirmation | Trust remote stdout for authorization |
| Capability / executor | Connect, exec, record evidence | Skip host verify or policy |
| Transport | Bytes on the wire | Business authorization |

## Risk classes

`READ_ONLY` · `LOW_RISK` · `MODIFYING` · `PRIVILEGED` · `DESTRUCTIVE` · `UNKNOWN`

UNKNOWN and DESTRUCTIVE never auto-approve for agents. DESTRUCTIVE is blocked even under automation policy overrides.

## Verification

Exit code alone is insufficient. Plans include explicit verification steps; `run_plan_steps` stops on failed/denied critical steps.
