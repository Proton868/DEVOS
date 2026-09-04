# Specialty UCIP policy

Declarative profiles per persona (web, code, design, research, …).

**Not** a second authorization engine. Tool execution still passes through UCIP / Agent Runtime.

Effective caps ≈ specialty allow ∩ ¬deny ∩ ¬ALWAYS_BLOCKED ∩ trust ceiling.

```mermaid
flowchart TD
  A[Node requested caps] --> B[Specialty allow/deny]
  B --> C[ALWAYS_BLOCKED / HITL sets]
  C --> D[Trust ceiling]
  D --> E{Decision}
  E -->|allow| F[Narrowed caps to Agent Runtime]
  E -->|deny| G[BLOCKED]
  E -->|HITL| H[AWAITING_APPROVAL]
```

Lower layers never broaden higher-layer denies.
