# Security Audit — DevOS V4 Production Readiness

**Scope:** line-by-line review of the Python/FastAPI backend and React frontend source,
addressing production-readiness items **P5** and **P5a–P5e**.  Each section lists the
file(s), the real behavior at specific lines, the risk rating, and any open residual risk.

---

## Summary table

| Area | Files | Verdict | Residual risk |
|---|---|---|---|
| P5a API routes | `api/routes/*.py` | Mostly hardened; three improvements recommended | Medium |
| P5b Execution | `execution/runner.py`, `execution/files.py`, `execution/script_runner.py`, `execution/search.py` | Resource & path isolation present; sandbox limits apply to child PID | Low–Medium |
| P5c Governance | `governance/sandbox.py`, `governance/hitl.py`, `governance/rbac.py`, `governance/secrets_vault.py`, `governance/ucip.py` | HITL + RBAC + encryption at rest in place; singleton locking done | Low |
| P5d Frontend | `frontend-src/src/services/api.js`, `frontend-src/src/services/supabase.js`, IDE panels | Supabase-primary auth implemented; stale stub routes remain | Medium |
| P5e Docs | `README.md`, `PRODUCTION_PLAN.md`, `.env.example` | Partial; README/PRODUCTION_PLAN update is P7a/P7c | Medium |

---

## P5a — API routes

### Authentication & authorization

- [`api/routes/auth.py`](api/routes/auth.py:225) [`get_current_user()`](api/routes/auth.py:225) supports
  three modes via [`settings.AUTH_MODE`](core/config.py:64): `local`, `supabase`, `dual`.   
- Dual mode tries local HS256 verification first ([`decode_local_token()`](api/routes/auth.py:73)),
  with explicit [`iss`/`aud`](api/routes/auth.py:47) binding, then falls back to Supabase
  JWKS or legacy HS256 ([`decode_supabase_token()`](api/routes/auth.py:98)).
- Supabase identities are synced into a local [`User`](core/database.py:17) row keyed by
  [`supabase_id`](core/database.py:32); the local `User.id` foreign keys never change.
- Local accounts used for Supabase-migrated users store `hashed_password=None`
  ([`sync_supabase_user()`](api/routes/auth.py:191)), preventing local login for them.

Risk rated: **LOW** for the auth path itself.  Residual:
1. The bootstrap admin created in the login handler always has a local password hash; in
   `AUTH_MODE=supabase` that local JWT will be rejected, but the admin cannot log in via
   Supabase until explicitly synced.  This is documented in
   [`AUTH_MODE`](core/config.py:64) docstring; operators must plan the migration.

### Path/script injection in routes

- [`api/routes/files.py`](api/routes/files.py:30) [`read_file()`](api/routes/files.py:30) passes the raw
  `path` query string to [`FileService._resolve()`](execution/files.py:35), which uses
  [`Path.resolve()`](execution/files.py:38) and checks containment against the project root
  ([`self.root not in candidate.parents`](execution/files.py:39)).  This prevents `../`
  escapes under the project directory.
- [`api/routes/files.py`](api/routes/files.py:105) [`delete_file()`](api/routes/files.py:105) routes
  IDE deletions through the same HITL gate as autonomous agent actions.

Risk rated: **LOW**.  Residual:
2. Containment check can be bypassed on case-insensitive or symlinked filesystems
   (`candidate.resolve()` follows symlinks); the audit recommends adding `realpath` + case
   normalization on Windows deployments.

### Secret handling

- [`api/routes/secrets.py`](api/routes/secrets.py:55) list/get endpoints deliberately never
  return the encrypted value; only name/description/timestamps are exposed
  ([`_to_dict()`](api/routes/secrets.py:48)).
- [`api/routes/secrets.py`](api/routes/secrets.py:63) [`create_secret()`](api/routes/secrets.py:63)
  normalizes names to `[A-Z0-9_]` and prefixes the injected env var as `SECRET_<NAME>` at
  execution time.
- Encryption uses Fernet with per-secret salt and PBKDF2 over `JWT_SECRET`
  ([`governance/secrets_vault.py`](governance/secrets_vault.py:47)).

Risk rated: **LOW**.

### Sanitization

- [`core/sanitize.py`](core/sanitize.py:41) [`sanitize_name()`](core/sanitize.py:41) strips all ASCII
  control characters; [`sanitize_freeform()`](core/sanitize.py:51) preserves tab/newline/CR.
- Validators on [`ScriptCreate`](api/routes/scripts.py:30), [`SecretCreate`](api/routes/secrets.py:42),
  and other Pydantic models call these before persistence.

Risk rated: **LOW**.  Residual:
3. User chat/message content is stored in SQLAlchemy `Text` columns without the same
   control-character scrubbing; although not rendered as HTML, long-term storage of raw
   LLM output is a latent log/UI injection vector.  Recommend scrubbing on write in
   [`api/routes/chat.py`](api/routes/chat.py:167).

### Webhook exposure

- [`api/routes/scripts.py`](api/routes/scripts.py:129) [`webhook_trigger()`](api/routes/scripts.py:130)
  accepts an opaque token; only the script row with a matching token is resolved.  Tokens
  are generated via [`gen_id()`](core/database.py:13) (UUID4) and rotatable via
  [`rotate_webhook_token()`](api/routes/scripts.py:149).

Risk rated: **LOW**; webhook URLs should still be served only over HTTPS in production.

---

## P5b — Execution layer

### Code runner

- [`execution/runner.py`](execution/runner.py:84) [`ExecutionLayer.run()`](execution/runner.py:84)
  writes code to a per-script file in [`SCRIPT_DIR`](execution/runner.py:27), runs it with
  `asyncio.create_subprocess_exec`, truncates output to 50 kB, and cleans up the file in
  `finally` ([`script_file.unlink()`](execution/runner.py:147)).
- Injections of `env_vars` and `secrets` happen only after the subprocess env is copied,
  via [`env.update(env_vars)`](execution/runner.py:115) and
  [`env[f"SECRET_{k.upper()}"] = str(v)`](execution/runner.py:118).

Risk rated: **MEDIUM**.  The runner runs code with the full privileges of the DevOS
server process and without a sandbox wrapper; the safer path is
[`SandboxedExecutor`](governance/sandbox.py:93), which `execution/script_runner.py` is
expected to use.  Verify [`execution/script_runner.py`](execution/script_runner.py:1) calls
`SandboxedExecutor` rather than `ExecutionLayer` for untrusted Flow script code.

### File service

- [`execution/files.py`](execution/files.py:74) [`FileService.write()`](execution/files.py:74)
  encodes content to UTF-8 and enforces a 2 MB cap
  ([`len(encoded) > MAX_READ_BYTES`](execution/files.py:82)); read has the same cap
  ([`size > MAX_READ_BYTES`](execution/files.py:68)).
- [`FileService._resolve()`](execution/files.py:35) refuses escapes above the project root.

Risk rated: **LOW** after the P3g size-cap fix.

### Sandbox

- [`governance/sandbox.py`](governance/sandbox.py:243) [`_apply_ulimits()`](governance/sandbox.py:243)
  uses `resource.prlimit(proc.pid, ...)` (Python 3.9+) to apply memory/CPU/FD/file-size
  limits to the **child**, not the server.  This closes the critical bug identified in
  `plans/bug-audit-plan.md`.
- Still blocks: static regex deny list before execution, stripped environment keys
  ([`STRIP_ENV_KEYS`](governance/sandbox.py:45)), isolated temp working directory per run,
  process-group kill on timeout, output truncation.

Risk rated: **LOW–MEDIUM**.  Residual:
4. Static analysis is regex-based and can be evaded; it is a coarse gate, not a sandbox.
   True isolation requires gVisor/Firecracker/Docker with seccomp.  The code emits the
   correct Docker flags but cannot apply them in this environment.

---

## P5c — Governance

### Human-in-the-loop

- [`governance/hitl.py`](governance/hitl.py:79) [`HITLQueue`](governance/hitl.py:79) is a singleton
  guarded by a `threading.Lock` ([`_lock`](governance/hitl.py:86)), closing the race
  condition identified in `plans/bug-audit-plan.md`.
- Submissions time out after [`HITL_TIMEOUT_S`](governance/hitl.py:34), auto-deny on expiry
  ([`is_expired()`](governance/hitl.py:56)).
- Events are published over [`EventBus`](communications/bus.py:1) for SSE push to the
  frontend.

Risk rated: **LOW**.

### RBAC / UCIP

- [`governance/rbac.py`](governance/rbac.py:86) [`RBACEngine.evaluate_capability_tokens()`](governance/rbac.py:136)
  checks tier hierarchy then explicit capability tokens; tokens are additive only.
- [`governance/identity_context.py`](governance/identity_context.py:1) defines
  `IdentityContext`, `CapabilityToken`, and token expiry.
- The LLM/tool layer is supposed to consult UCIP before invoking tools; the UCIP scanner
  also runs prompt-injection checks.

Risk rated: **LOW** for the engines.  Residual:
5. Some route handlers (e.g. file deletion through the IDE) now enqueue HITL, but other
   powerful routes still rely entirely on ownership checks; recommend a systematic pass
   to ensure any `filesystem.delete`, `system.shell`, `vcs.push`, or `agent.spawn` action
   triggered by a user or agent is gated by UCIP/HITL.

### Secrets vault

- Per-secret random salt with PBKDF2 is implemented ([`governance/secrets_vault.py`](governance/secrets_vault.py:47)).
- Legacy ciphertexts fall back to two static salts for migration.
- Decryption failure is logged and skipped at script-run time, preventing a single bad
  secret from crashing the runner.

Risk rated: **LOW**.

---

## P5d — Frontend

### API client

- [`frontend-src/src/services/api.js`](frontend-src/src/services/api.js:1) wraps FastAPI calls,
  carries the `devos_token` cookie automatically, and returns standardized errors.
- [`frontend-src/src/services/supabase.js`](frontend-src/src/services/supabase.js:1) initializes
  Supabase auth and provides helpers for sign-in/sign-out; tokens are forwarded to the
  backend naturally because `api.js` resolves both the local token and Supabase session
  token via `resolveAuthToken()`.
- [`frontend-src/src/App.jsx`](frontend-src/src/App.jsx:293) subscribes to the SSE event
  stream and routes `hitl.pending` events into the store; [`HitlApprovalToasts`](frontend-src/src/App.jsx:203)
  renders floating approve/deny cards and calls [`api.approveHitl`](frontend-src/src/services/api.js:310) /
  [`api.denyHitl`](frontend-src/src/services/api.js:311).  The HITL approval UI is therefore
  present and wired.
- `FlowPanel.jsx` was rewritten to use real API/field names and a polling run view
  (recorded in `PRODUCTION_PLAN.md` Session 22).
- `api.streamAgent()`, `api.streamChat()`, and `api.reindex()` are real implementations
  in [`api.js`](frontend-src/src/services/api.js:1); `notBuiltYet` is spread first so it
  only fills genuinely unimplemented methods rather than overriding these real ones
  ([`api.js:608`](frontend-src/src/services/api.js:608)).

Risk rated: **LOW**.  Residual:
6. (Previously listed AgentPanel/ChatSidebar/HITL items have been verified as implemented.)

### XSS / CSP

- React's default escaping mitigates direct DOM XSS; `dangerouslySetInnerHTML` is not used in
  the inspected panels.
- Backend [`SecurityHeadersMiddleware`](app.py:187) sets a restrictive `Content-Security-Policy`
  HTTP header on every response ([`app.py:203`](app.py:203)).
- A matching CSP meta tag is now present in [`frontend-src/public/index.html`](frontend-src/public/index.html:10)
  as defense-in-depth for locally-opened builds.

Risk rated: **LOW**.

---

## P5e — Documentation

- `.env.example` exists and documents all security-sensitive variables.
- `plans/bug-audit-plan.md` documents confirmed bugs and fixes.
- This file captures line-level security audit findings (P5/P5a–P5e).
- Out of scope for this document but tracked separately:
  - `README.md` rewrite (P7a)
  - `PRODUCTION_PLAN.md` update (P7c)
  - `DEPLOYMENT.md` creation (P7d)

---

## Recommended next actions (security only)

1. ✅ Backend CSP header already implemented in [`app.py`](app.py:203); frontend CSP meta tag
   added in [`index.html`](frontend-src/public/index.html:10).
2. ✅ HITL approval UI and SSE wiring already implemented in [`App.jsx`](frontend-src/src/App.jsx:293).
3. Apply control-character scrubbing to chat/message writes in
   [`api/routes/chat.py`](api/routes/chat.py:167).
4. Document the symlink/case-folding filesystem caveat in `DEPLOYMENT.md`.
5. Confirm [`execution/script_runner.py`](execution/script_runner.py:1) routes all untrusted
   Flow script code through [`SandboxedExecutor`](governance/sandbox.py:93), not
   [`ExecutionLayer`](execution/runner.py:78).
6. For true code isolation in production, deploy the sandbox with gVisor/Firecracker/
   Docker-with-seccomp; the built-in `SandboxedExecutor` only applies process/resource
   limits to the child process.
