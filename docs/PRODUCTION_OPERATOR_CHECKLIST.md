# Production Operator Checklist (prime)

**Audience:** human operator on production host `prime`  
**Coding agent does not run these steps.**

Use labels: **PASS** | **FAIL** | **UNPROVEN**

Do not mark a step PASS without direct evidence from this host.

---

## Prerequisites

- SSH access to `prime`
- Repo path (typical): `/home/ubuntu/devos` (confirm on host)
- Working `.env` with production values (never commit secrets)

---

## A. Update DevOS from Git

```bash
cd /home/ubuntu/devos   # or actual install path
git status --short --branch
git fetch origin
git rev-parse HEAD
# Preferred: full ops path
bash ops/update.sh
# Or: git merge --ff-only origin/main && bash ops/apply_migrations.sh && systemctl restart devos
git rev-parse HEAD
```

Record: commit SHA after update.

---

## B. Verify production configuration

```bash
# Does not print secret values
.venv/bin/python ops/env_validate.py --env-file .env --production --require-file
```

Confirm fail-closed expectations:

| Variable | Expected |
|----------|----------|
| `REQUIRE_POSTGRES` | true |
| `DATABASE_URL` | Postgres/Supabase (not SQLite) |
| `DEFAULT_PROVIDER` | omniroute |
| `DEBUG` | false |
| `DEVOS_ORCH_FAKE_RUNTIME` | unset / false |
| `DEVOS_ALLOW_WEBSITE_SCAFFOLD_FALLBACK` | not `1` |
| `AUTH_ENABLED` | true |
| `JWT_SECRET` | strong, non-default |

---

## C. Run production verification

```bash
bash ops/verify.sh --production
curl -fsS https://dev.carai.agency/api/health   # or local :8000 if applicable
```

Health should show (when deployed with current code):

- `db_backend=postgres` (or equivalent)
- `memory_backend=postgres`
- subsystem backends postgres where reported
- `fake_runtime_env=false`
- `status` not `fail`

---

## D. Authenticate

- Sign in as a **dedicated test user** (not the owner admin if avoidable)
- Confirm unauthenticated protected routes return **401/403**

---

## E. Execute one real mission

Request (example):

> Create a harmless one-page website for fictional business "Footwalk Test".

Expect spine:

```text
Human → Nuha → plan → durable mission → A2A → AgentRuntime → artifact → Ponytail → evidence → acceptance → truth
```

Record redacted: `mission_id`, `task_id`, agent/persona, provider.

---

## F. Inspect durable state

Using app APIs / DB (service role only if authorized):

- [ ] Mission row exists and is terminal only after acceptance
- [ ] A2A messages exist for the mission
- [ ] Workspace files exist (not chat-only HTML)
- [ ] Ponytail check present and passed
- [ ] Evidence refs present
- [ ] Agent work history attributed correctly

---

## G. Idempotency

- Repeat the **same** request with the same client idempotency key (if API supports it)
- Expect **same** mission (no duplicate authoritative work)
- Different key → distinct mission

---

## H. Ownership

- Second test user must **not** read/modify first user’s mission, artifacts, history
- Expect **403** or indistinguishable **404**

---

## I. UCIP denial

- Attempt an unauthorized capability/tool if test harness exists
- Expect denial; denial must **not** become authorization or mission success

---

## J. Safe restart / recovery

```bash
sudo systemctl restart devos
# wait for healthy
curl -fsS https://dev.carai.agency/api/health
```

- Re-fetch the mission from E — durable fields still present
- Optional: interrupt a mid-flight mission and confirm reconcile rules (resume Ponytail / evidence / complete)

---

## K. Re-run verification

```bash
bash ops/verify.sh --production
```

---

## L. Record evidence (redacted)

Store for the acceptance log:

- Commit SHA
- Health JSON (no secrets)
- Mission IDs (redact user emails)
- PASS/FAIL/UNPROVEN per gate in `docs/PRODUCTION_E2E_ACCEPTANCE.md`

---

## Explicit non-claims

Automated CI / coding-agent sandbox results are **not** production proof.

Mocks and `DEVOS_ORCH_FAKE_RUNTIME` are **forbidden** for production acceptance.

## Auth smoke (after restart)

```bash
curl -sS -D- http://127.0.0.1:8000/api/auth/public-config -o /tmp/pc.json | head -15
# Expect: HTTP 200, content-type application/json
# Body: supabase_configured, supabase_url, supabase_anon_key (no service_role)
python3 -c "import json;d=json.load(open('/tmp/pc.json')); assert 'supabase_anon_key' in d; print('auth public-config OK')"
```

Record: **PASS** / **FAIL** / **UNPROVEN**

## Strong isolation (untrusted project commands)

Live health must show `suitable_for_untrusted_code: true` before Flutter/npm/pytest
project commands can run under policy.

```bash
# On prime (operator)
bash ops/enable_strong_isolation.sh
# typical: apt install bubblewrap  OR  Docker + DEVOS_USE_DOCKER_SANDBOX=1
systemctl restart devos
curl -fsS https://dev.carai.agency/api/health | jq .isolation
```

Do **not** set `DEVOS_ALLOW_DEGRADED_ISOLATION=1` to force host execution.


## Provider Settings (admin)

OmniRoute keys editable via Settings (admin): `OMNIROUTE_BASE_URL`, `OMNIROUTE_API_KEY`, `OMNIROUTE_DEFAULT_MODEL`.  
`SUPABASE_KEY` is **not** editable via the Settings API (service-role risk).
