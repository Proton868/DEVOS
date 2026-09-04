# Identity & Isolation Threat Model

**Canonical identity flow**

```
LOCAL AUTH / SUPABASE
 → AUTH SESSION (verified JWT)
 → DEVOS ACCOUNT (users.id)
 → DevOSIdentity (subject, account, tenant/workspace scope, role, plan)
 → PERSONA (behavior only)
 → SPECIALTY POLICY
 → UCIP
 → EXECUTION
```

Frontend React state and localStorage are **not** authority.

## Canonical fields

| Field | Meaning |
|-------|---------|
| subject_id | Authenticated principal |
| account_id | `users.id` |
| tenant_scope | Personal/org tenant membership |
| workspace_scope | Authorized project roots under `data/projects/{user_id}/` |
| role | member \| elder \| hegemon (label) |
| plan | recruit \| outer_sect \| inner_sect \| conclave \| hegemon (entitlement label) |
| persona | AI behavior — **not** authorization |
| XP | Experience only |

## Ownership model

- Profiles/account mutations: always `get_current_user` → mutate **self** only. Client `role`/`plan`/`account_id` stripped.
- Files: `FileService(user_id, project_id)` roots at `data/projects/{user_id}/{project_id}/` with path traversal guards.
- Web crawl **jobs**: `user_id` on crawl rows; list APIs filter by user. Low-level `get_crawl(id)` is not a public API without ownership check (API layer enforces).
- Jobs API: existing `owner_id` / tenant membership checks on job get.
- Shared **public web cache**: URL content only — not private crawl reports/credentials.

## Supabase RLS

Supabase is used for **authentication tokens** (and optional client). Primary durable app data lives in **DevOS SQLite/SQLAlchemy**.

| Layer | Role |
|-------|------|
| Supabase Auth JWT | Subject verification |
| Backend ownership | Service paths that bypass any client RLS |
| FileService | Artifact isolation |
| UCIP | Execution authority |

If/when user-owned rows are stored in Supabase Postgres, RLS must map `auth.uid()` → account ownership. Service-role keys never ship to the frontend (`SUPABASE_KEY` server-only).

**Service-role caveat:** any backend use of elevated Supabase credentials must still perform account ownership checks — RLS is bypassed by service role.

## Avatar

**IMPLEMENTED:** FileService-backed avatar upload at `POST /api/account/avatar` (user-owned `profile/avatar.*`).  
**ALSO:** `avatar_url` field points at `/api/account/avatar`. Cross-account GET by id → 404.

## Threat matrix (summary)

| Threat | Defense | Evidence |
|--------|---------|----------|
| Cross-account profile write | Self-only PATCH + strip authority fields | unit tests |
| Plan → Elder/Hegemon | PUBLIC_PLANS reject | unit tests |
| Cross-workspace files | per-user FileService root + PathViolation | unit tests |
| UCIP bypass | authorization_decision deny → blocked, no success | unit tests |
| Hostile client role claim | reject_client_authority_fields | unit tests |
| Crawl list leakage | list_crawls(user_id) | unit tests |
| IDOR job/mission | existing owner_id checks (Jobs API) | code audit |
| Service-role misuse | documented; ownership required | this doc |
| Avatar theft via upload key | N/A until upload exists | truthful status |

## Actors

T1 unauthenticated · T2 ordinary user · T3 malicious field injection · T4 compromised frontend · T5 stolen UUID · T6 privilege escalation · T7 cross-workspace execution · T8 service-role misuse · T9 storage object attack · T10 session attack

## Invariant

```
USER A WORLD  ╳ DENY ╳  USER B WORLD
unless explicit authorized admin/collaboration policy
```
