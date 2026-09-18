# DevOS current status

**Tip:** `06f45ed` lineage + follow-up fixes on main (OmniRoute editable keys, provider UI, isolation, tenant).  
**Product:** AI-native Development OS — Nuha orchestrates; specialists execute under UCIP.

## Implemented (code)

| Area | Status |
|------|--------|
| OmniRoute native gateway + Settings unified providers | Yes |
| OpenRouter / multi-provider routing, 429 backoff | Yes |
| Context budgets by task class | Yes |
| Mandatory untrusted isolation (fail-closed) | Yes |
| Real-runtime gate (forbids fake runtime) | Yes |
| Mission authority (`declare_mission_outcome` + acceptance) | Yes |
| Flutter toolchain + checksum pins + HITL provision | Yes |
| Toolchain profiles (Python…Ruby) | Yes |
| Tenant/RLS migrations (incl. notes/documents/layouts) | Yes (repo) |
| Enterprise `tenant_id` membership gate | Yes |
| Notes API owner-scoped | Yes |

## Production (live as of last operator check)

| Check | Status |
|-------|--------|
| Health / Postgres backends | Working |
| Fake runtime | Off |
| Strong isolation (`suitable_for_untrusted_code`) | **Often false** until bwrap/Docker on host |
| Live RLS `pg_policies` | **Operator must verify** |
| Auth / coding E2E | **Operator must verify** |

Default provider: **omniroute**. Ollama optional (not featured in Settings UI).

## Security invariants (do not weaken)

- Untrusted project commands → strong/restricted isolation only  
- Coding COMPLETED → acceptance + mission authority only  
- Service role / `SUPABASE_KEY` not Settings-UI editable; not browser public-config  
- Fake runtime forbidden in real-runtime gate and production health  

## Operator next steps

1. Deploy current `main` to Prime  
2. `bash ops/enable_strong_isolation.sh` → health shows suitable untrusted  
3. Apply migrations; inspect `pg_policies`  
4. Authenticated coding + cross-user IDOR tests  
