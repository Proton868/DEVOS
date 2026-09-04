# DevOS Final Architecture Invariant

```
Mission decides WHAT
→ Specialty Policy restricts domain
→ capability canon normalizes
→ UCIP decides WHETHER
→ Execution DOES
→ Verification / Evidence PROVES
→ Nuha decides recovery
→ Saga COMPENSATES SAFELY
→ Outbox DURABLY PUBLISHES STATE
→ OpenTelemetry OBSERVES
```

## Authorities (do not confuse)

| Component | Role |
|-----------|------|
| **UCIP** | Authorization authority |
| **Specialty Policy** | Domain restriction |
| **Mission Engine + DAG** | What runs, order, readiness |
| **Saga** | Recovery / compensation coordination |
| **Outbox** | Durable event delivery after commit |
| **OpenTelemetry** | Optional observability export |
| **DevOS TraceContext** | Product-level correlation |

Saga, Outbox, and OTel **never** authorize execution.

## Saga pivot semantics

- **COMPENSABLE** — may be automatically compensated if ownership/preconditions hold and UCIP allows.
- **PIVOT** — durable point of no return (e.g. `github_push`). Recorded with `pivot_reached`, `pivot_step_id`, `pivot_at`.
- **POST_PIVOT** — prefer forward recovery / manual remediation; do not pretend external world is restorable.

Crash after successful pivot must **not** cause restart to attempt rollback of the pivot action.

## Compensation

1. Policy matrix + resource ownership/version checks  
2. `authorize_compensation` → **UCIPGateway.request**  
3. Execute only if allowed  
4. Evidence + outbox  

Unsupported provider reverse operations → `MANUAL_REMEDIATION`.

## Outbox

Same SQLite durable DB. Transactional intent:

```
BEGIN
  UPDATE domain state
  INSERT outbox_events
COMMIT
```

Dispatcher claims → delivers → marks delivered; bounded retry; idempotency keys.

## Tracing

DevOS `TraceContext` is product authority. OTel bridges for export.  
`traceparent` is **not** identity, authentication, or capability.
