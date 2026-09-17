# Hardening

- UCI authorize_capability_slug
- AgentIdentity.create no elevation
- Provider secrets masked; admin for PUT
- Chat/script ownership checks
- execution/isolation.py network-off paths
- Tenant/Membership models
- Supabase RLS SQL + apply script


## Current freeze

Governance v1 and Reliability architecture v1 are frozen. Prefer README.md, docs/CURRENT_STATUS.md, and docs/STAGING_DRILLS.md over older audit prose where they conflict.

## Auth surface hardening

- `GET /api/auth/public-config` is browser-visible JSON only (anon key, never service_role).
- SPA catch-all must return JSON 404 for `/api/*`, not React HTML.
- Prefer Supabase Auth for interactive login; DevOS remains the authorization authority.
- See [AUTH_AND_ISOLATION.md](AUTH_AND_ISOLATION.md).


## Mandatory isolation model (2026-09-17)

| Trust level | Meaning | Required isolation strength |
|-------------|---------|----------------------------|
| **trusted** | Local human / developer IDE terminal | May use host when explicitly intended |
| **untrusted** | AI-generated, uploaded, project, bootstrap, check, coding | **strong** or **restricted** only |
| **privileged** | Host/system/deploy high-risk | **strong** or **restricted** only |

- `network_only` (unshare --net) and `degraded` host **fail closed** for untrusted and privileged.
- No silent fallback to bare host for agent/project commands.
- Canonical path: `run_command_in_project` → `run_governed` → `run_isolated`.
- Project sources cannot spoof `policy=trusted` (classify forces untrusted).
- Refusal contract: `status=isolation_unavailable`, `ok=false`, structured `isolation_evidence`.

Backends (preference): Docker (`DEVOS_USE_DOCKER_SANDBOX=1`) → bubblewrap → firejail → unshare (network_only only) → none.

Human `TerminalService` remains trusted-local shell with env scrub; it is not the agent command path.



## Subprocess inventory (containment)

| Class | Paths | Boundary |
|-------|-------|----------|
| **1 UNTRUSTED PROJECT** | `run_command_in_project` → `run_governed` → `run_isolated`; AgentRuntime; check_runner; project_bootstrap | strong/restricted only; scrub_env; no host fallback |
| **2 TRUSTED HUMAN TERMINAL** | `TerminalService`, `PtySession` (IDE) | Human-owned project cwd; scrub_env (no service secrets); not agent path |
| **3 PRIVILEGED INFRASTRUCTURE** | marketplace npm/pip install jobs; PyRunner venv (authority-gated) | Scrubbed env; not open to model-controlled shell |
| **4 FIXED INTERNAL** | LSP, MCP, cloudflare tunnel, app_runtime, vcs argv, flutter version probe | Fixed argv/binaries |
| **5 TEST-ONLY / OPS** | `scripts/*`, `cli.py` | Operator machine |
| **6 DEAD** | — | — |

Agent tools use `run_command` only through AgentRuntime (class 1). Human terminal APIs are class 2 and must not be invoked by agents.
