# HTTP Endpoint Ownership Inventory (discovered via FastAPI app.routes)

Status values: **PASS** | **FAIL** | **BLOCKED — ENVIRONMENT** | **NOT HTTP-EXPOSED** | **UNSUPPORTED — NO ENDPOINT**

## Core identity / account (tested)

| Resource | Method | Path | Auth | Ownership | Tested |
|----------|--------|------|------|-----------|--------|
| Auth me | GET | /api/auth/me | yes | self | PASS |
| Account me | GET | /api/account/me | yes | self | PASS |
| Profile | PATCH | /api/account/profile | yes | self only | PASS |
| Plan | POST | /api/account/plan | yes | self; no elder/hegemon | PASS |
| Avatar | POST | /api/account/avatar | yes | self FileService | PASS |
| Avatar | GET | /api/account/avatar | yes | self | PASS |
| Avatar by id | GET | /api/account/avatar/{account_id} | yes | self only | PASS |

## Files (tested)

| Resource | Method | Path | Tested |
|----------|--------|------|--------|
| Write | POST | /api/files/{project_id}/write | PASS |
| Read | GET | /api/files/{project_id}/read | PASS (cross-user no leak) |

Other file methods (list/tree/delete/preview/upload/…) share FileService(user_id, project_id) — same ownership model; not every verb re-listed as separate PASS.

## Web crawls (tested)

| Method | Path | Tested |
|--------|------|--------|
| GET | /api/web/crawls/{crawl_id} | PASS |
| POST | .../cancel | PASS |
| GET | .../pages, events, report | PASS |
| POST | .../resume | PASS |

## Voice (Carai) (tested when create works)

| Method | Path | Tested |
|--------|------|--------|
| POST | /api/carai/sessions | PASS if available |
| GET | /api/carai/sessions/{session_id} | PASS IDOR |

## Jobs (tested when enqueue works)

| Method | Path | Tested |
|--------|------|--------|
| POST | /api/jobs | PASS if available |
| GET | /api/jobs/{job_id} | PASS IDOR |

## Agent tasks

| Method | Path | Tested |
|--------|------|--------|
| GET | /api/agent/{task_id} | PASS (missing id → 403/404; live cross-user when task exists relies on user_id check in agent.py) |
| POST | /api/agent/{task_id}/cancel | PASS shape |

## Explicitly not fabricated

| Resource | Status |
|----------|--------|
| “Mission” as separate /api/missions/* | UNSUPPORTED — NO ENDPOINT (orchestration uses /api/orchestration/{plan_id}) |
| Supabase Postgres RLS tables | NOT HTTP-EXPOSED (SQLite app data) |

## Orchestration / chat / research / delivery / secrets / …

Discovered in OpenAPI (auth-gated). Full per-route cross-account matrix not exhaustively automated in this pass; ownership patterns: `get_current_user` + resource.user_id/owner_id. Expanding remaining verbs is incremental test work without new architecture.

Unauthenticated access to protected account/files routes: **PASS** (401/403).
