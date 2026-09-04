# Nuha Orchestration Architecture

## Responsibility boundaries

| Layer | Responsibility |
|-------|----------------|
| **Nuha** | Intelligence, planning, orchestration |
| **DAG** | What depends on what |
| **UCIP + specialty policy** | Whether it is permitted |
| **Jobs / Agent Runtime** | How/when work executes |
| **Verification** | Whether it actually worked |
| **XP** | Experience only — never authority |
| **Spatial UI** | Presentation |

## Flow

```text
User → Nuha → Chat | Plan | Action
                ↓
         OrchestrationPlan + DAG
                ↓
    Specialty policy ∩ UCIP constants
                ↓
      Existing Agent Runtime / Jobs
                ↓
           Verification → XP
```

Plan mode never writes. Action mode never skips UCIP.
