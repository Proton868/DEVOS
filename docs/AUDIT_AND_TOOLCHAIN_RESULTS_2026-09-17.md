# DevOS audit & toolchain results — 2026-09-17

This document records outcomes from the multi-tenant security audit and
governed Flutter toolchain work landed on `main` as `db19b8e` and follow-ups.

---

## 1. Multi-tenant isolation audit

### Classification

| Code | Meaning | Result |
|------|---------|--------|
| **B** | Tenant-safe at application layer; RLS incomplete before remediation | **Primary result** |
| **D** | Cannot fully verify production schema without live DB access | Residual |

Ordinary authenticated users are blocked by route-level ownership on audited
surfaces. SaaS multi-tenant certification is **not** claimed without staging
`pg_policies` verification after migration apply.

### Architecture

| Layer | Role |
|-------|------|
| FastAPI + SQLAlchemy | Primary SoT via privileged `DATABASE_URL` (bypasses RLS) |
| Supabase Auth | JWT identity; `users.supabase_id` linkage |
| RLS | Defense-in-depth if tables exposed via PostgREST |
| Filesystem | `data/projects/{user_id}/{project_id}/` via `FileService` |

### Tables audited (≈55)

Identity: `users`, `tenants`, `memberships`  
Chat: `chat_sessions`, `messages`  
Scripts/content: `scripts`, `script_*`, `notes`, `documents`  
Secrets/settings: `secrets`, `user_settings`, `workspace_layouts`, `custom_endpoints`  
Personas/orch: `persona_*`, `orchestration_plans`  
Workflow/evidence/jobs: `workflow_records`, `evidence_records`, `execution_*`  
Agents/missions/A2A: `agents`, `missions`, `mission_tasks`, `task_delegations`, `agent_*`, `tool_executions`, `ponytail_checks`  
Artifacts: `artifacts`, `artifact_versions`  
Memory: `memories`, `knowledge_*`  
Ops: `outbox_events`, `sagas`, `audit_log`, `web_crawls*`, `delivery_*`, `observability_*`, `durable_capabilities`, `worker_trust_records`

### Critical findings (pre-fix)

1. **`memories` RLS bug** — policies compared `auth.uid()` directly to app `user_id` (wrong; must use `users.supabase_id`).
2. Many tables had RLS enabled with **no policies** (deny-all for JWT; incomplete for intentional client access).
3. **`secrets` / `scripts` / settings** not in original RLS enable list (ORM-only).
4. **ORM vs SQL drift** — `workflow_records` SQL `user_id` vs ORM `owner_id`.
5. No production schema dump in audit environment.

### Application layer (sampled)

| Surface | Auth | Ownership | Spoof protection |
|---------|------|-----------|------------------|
| Secrets | Yes | `owner_id == user.id` | Strong |
| Chat | Yes | Session `user_id` | Strong |
| Files/artifacts | Yes | `FileService(user.id, project_id)` + traversal reject | Strong |
| Workflow | Yes | `*_for_owner`; strips client owner fields | Strong |
| Evidence | Yes | `_require_chain_owner` | Strong |
| Jobs | Yes | `owner_id` check | Strong |
| Agent tasks | Yes | `task.user_id` | Strong |

No confirmed app-layer IDOR on audited routes for ordinary authenticated users.

### Remediation migrations

- `supabase/migrations/20260917110000_orm_sql_column_alignment.sql`  
  — `workflow_records.owner_id` backfill; ensure `secrets`/`scripts`/`user_settings`/`custom_endpoints` exist
- `supabase/migrations/20260917120000_tenant_isolation_rls_completion.sql`  
  — fix memories; owner policies; guarded RLS for optional tables

### Staging apply

```bash
python3 scripts/apply_supabase_migrations.py
# SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public' ORDER BY 1,2;
```

### Remaining limitations

1. Production schema not verified in audit environment.
2. Org/agency multi-tenancy still largely personal-tenant oriented.
3. HTTP IDOR matrix expanded, not every route/method pair.
4. Legacy `user_id` on workflow_records retained; policies use `COALESCE(owner_id, user_id)`.

---

## 2. Governed Flutter toolchain

### ToolchainProfile

| Field | Value |
|-------|--------|
| `install_cmd` | `flutter pub get` |
| `typecheck_cmd` | `flutter analyze` |
| `test_cmd` | `flutter test` |
| `build_cmd` | `flutter build apk --debug` |
| `required_binaries` | `flutter`, `dart` |

Detection: `pubspec.yaml` + `sdk: flutter`, `lib/`, platform dirs.  
Missing SDK → `toolchain_unavailable` (no silent install).

### SDK provisioning

| Aspect | Design |
|--------|--------|
| Capability | `ucip:toolchain.flutter_sdk_provision` (ALWAYS_HUMAN_GATED) |
| Install root | `data/toolchains/flutter` |
| Network | HTTPS allowlist: `storage.googleapis.com/flutter_infra_release/releases/` |
| Forbidden | sudo, apt, brew, curl\|bash, host PATH mutation, `/usr` installs |
| Safety | checksum pins, path-traversal + symlink-safe extract, concurrent lock, idempotent, partial cleanup |

**Trust boundary:** SDK install trust ≠ project code trust (untrusted isolation still required).

### API / agent tools

- `GET /api/toolchain/flutter`
- `POST /api/toolchain/flutter/provision` (HITL by default)
- Agent: `probe_flutter_runtime`, `provision_flutter_sdk`

### Platform limits

- `ios` / `macos` builds require **macOS + Xcode**
- Documented in `platform_build_limits()` and `docs/FLUTTER_TOOLCHAIN.md`

### Tests

- `tests/test_flutter_toolchain.py` — offline unit suite (detect, auth, checksum, traversal, symlink, concurrent, idempotent, no sudo, isolation)
- `tests/test_tenant_isolation_rls_matrix.py` — migration predicate + FileService checks
- `tests/real_runtime/test_flutter_real_runtime.py` — skips if Flutter absent on host

---

## 3. Threat tests exercised (design intent)

Authenticated ordinary user must not:

1. Read/insert/update/delete another user’s rows  
2. Change `owner_id` / `user_id`  
3. Access foreign projects, files, missions, agent tasks, evidence, secrets, workflows, memory, A2A state  
4. Abuse SECURITY DEFINER / service-role paths as arbitrary query  
5. Install Flutter via sudo/apt/curl\|bash  

Flutter-specific: unauthorized SDK provision rejected; checksum/traversal/symlink failures fail closed.

---

## 4. Commits

| Commit | Summary |
|--------|---------|
| `db19b8e` | feat: governed Flutter toolchain and tenant RLS boundaries |

Prior related hardening already on main: isolation for untrusted execution, governed subprocess security, mission authority races.

---

## 5. Operator checklist

1. Apply Supabase migrations on staging; verify `pg_policies`.
2. Optionally fill `data/toolchains/flutter_checksums.json` with official SHA-256 pins.
3. On a host with Flutter installed, run `pytest tests/real_runtime/test_flutter_real_runtime.py`.
4. Exercise HITL approve/deny for `POST /api/toolchain/flutter/provision`.
