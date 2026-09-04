# Node state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> BLOCKED_BY_DEPENDENCY
    BLOCKED_BY_DEPENDENCY --> READY
    PENDING --> READY
    READY --> AUTHORIZATION_PENDING
    AUTHORIZATION_PENDING --> AUTHORIZED
    AUTHORIZATION_PENDING --> AWAITING_APPROVAL
    AUTHORIZATION_PENDING --> BLOCKED
    AUTHORIZED --> QUEUED
    QUEUED --> RUNNING
    RUNNING --> VERIFYING
    VERIFYING --> VERIFIED
    VERIFIED --> COMPLETED
    RUNNING --> FAILED
    FAILED --> RECOVERING
    RECOVERING --> REPLANNING
    REPLANNING --> READY
```

## Invariants

- RUNNING requires job ref, persona, authorization decision
- AUTHORIZED requires decision = allow
- VERIFIED requires verification_evidence
- BLOCKED requires blocking_reason
