# PRODUCTION PLAN — status banner

> **Docs sync:** See [README.md](README.md) and [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) for the live install path and implemented surfaces.  
> **Runtime targets:** Python **3.13** (min 3.11), Node **22** for UI rebuilds only. Prebuilt `frontend/` is required for zero-Node run.  
> **Research:** Prefer Hermes / `brain/research.py` naming over legacy Odysseus labels.  
> Historical stage checklists below may lag; do not treat unchecked boxes as “missing install.”

---

# Path to Production-Grade: Staged Completion Plan

Written after a real audit (Session 22), not a guess. Every item below was
either confirmed broken by actually testing it, or confirmed real and
working by actually testing it — see `record.md` Session 22 for the
evidence behind each line. This plan exists so "keep going" has a concrete
order to follow, not just a vague direction.

**Two things this environment cannot do, ever, regardless of stage:**
run a real LLM (no network access to any provider from this sandbox), or
run a real browser / `docker build` (neither tool exists here). Every stage
below is scoped to what real engineering work is achievable and testable in
this sandbox; those three remain the standing exceptions, noted again at
the point they'd matter.

---

## Stage 0 — Done this session (Session 22), for real, tested

- **Execution path double-bug**: `execution/runner.py`'s `SCRIPT_DIR` was a
  relative path used both as subprocess `cwd` and to build the script's
  file argument — resolved twice, produced a nonexistent doubled path.
  **This broke every single script execution through `/api/scripts/{id}/run`.**
  Fixed to an absolute path. Verified: a real script now actually runs and
  returns real stdout.
- **Secrets management didn't exist at all.** Built a real `Secret` model,
  Fernet-encrypted at rest (key derived from the already-required
  `JWT_SECRET`, not a second secret to manage), real CRUD routes, and wired
  actual decryption + injection into both the manual-run and the
  scheduled-run execution paths. Verified: created a secret via the real
  API, ran a script that reads `os.environ["SECRET_<NAME>"]`, got the real
  decrypted value back — twice, once per execution path.
- **Scheduled automation was never actually live.** A script created with
  `schedule_type="cron"`/`"interval"` was silently inert until the next
  server restart — nothing called the scheduler's registration function
  outside of startup. Fixed `create`/`update`/`delete` to register/
  reschedule/unschedule live. Verified: created an interval script through
  the real API and watched it genuinely auto-execute three times over 7
  seconds with zero manual trigger, then confirmed it also survives and
  reloads correctly across a real server restart.
- **`FlowPanel.jsx` (the actual automation UI) was completely
  disconnected from reality** — its own separate fetch client pointed at
  `localhost:3001` (the Node backend retired in Session 1), a `/api/flow`
  path prefix that never existed on the real backend, and field names
  (`packages`, `schedule`, `scheduleType`, `s.status`) that don't match the
  real `Script` model at all. Also expected a live SSE-streaming run view
  the backend has never supported (`/run` is fire-and-forget; results only
  show up via polling `/runs`). Fully rewritten against the real API and
  real field names, with a genuine polling-based run view instead of a
  fake stream. **Verified end-to-end with a real compiled React component,
  rendered in jsdom, driving the actual live backend**: logged in for
  real, created a script through the real form, saved it via a real POST,
  watched it appear, clicked Run, watched it poll the real `/runs`
  endpoint, and saw the actual real stdout appear in the UI.
- Found and fixed a smaller bug in the same pass: `api.js`'s `flowStats()`
  filtered by a field (`s.enabled`) the real backend doesn't have; the
  real field is `is_active`.
- Ran the full, complete real README for `agency-agents` and corrected the
  scale figure again (230+ agents, 16 divisions — not 61, not the earlier
  "~284 remaining" estimate). Added 85 more personas via a disclosed,
  lighter-weight templated method (real specialty/when-to-use data,
  generated system prompt rather than individually fetched) — **106 total
  personas now**, and found + fixed 4 real routing-keyword gaps in that
  batch by testing sampled goals against it.
- Added `.env.example` (documented since Session 19, never actually shipped
  as a file until now).

**Not yet done, carried into the stages below**, roughly in priority order
for "production-grade automation workflows and full-stack websites":

---

## Stage 1 — Full-stack website generation, made reachable

`brain/builder.py`'s `ProjectBuilder` was audited this session for the
first time across 22 sessions and found to genuinely work — real files
generated file-by-file via the Brain, real syntax verification, real save
into the same project-directory convention the IDE's file tree already
uses. **But nothing in the frontend can trigger it.** This is the single
highest-priority remaining gap against your explicit ask ("full stack
websites easily").

1. Add `api.js` methods: `listStacks()`, `buildProject(spec)`,
   `getBuildStatus`/result retrieval, matching `api/routes/extras.py`'s
   real routes exactly (audit those routes' actual request/response shape
   first — don't assume, the FlowPanel lesson from this session applies
   here too).
2. Build a real "New Project" UI: pick a stack (fastapi/nextjs/react/vite/
   flutter/html), describe it, list desired features, trigger a real
   build, show progress, land in the file tree / IDE on completion.
3. Test with a scripted Brain first (same discipline as every other
   session), then — this is the one that actually matters — the first
   real test against an actual LLM, whenever this runs somewhere with
   network access to one. Expect real prompt-format issues on that first
   real run; that's expected, not a regression.

## Stage 2 — Close out the other broken/placeholder frontend panels

Found this session, not yet fixed:
- **`AgentPanel.jsx`** calls `api.streamAgent()`/`api.reindex()` — both are
  `notBuiltYet` stubs that throw. This panel is now fully superseded by
  the real `WorkersPanel` (Sessions 8-15) — retire it from the UI (remove
  its tab/shortcut) rather than fix it, so there's one working entry point
  for agent work, not a broken duplicate next to a real one.
- **`ChatSidebar.jsx`** calls `api.streamChat()` — also a stub. `api/routes/chat.py`
  exists on the backend; audit what it actually supports (does it
  stream? does it use the Brain loop or something simpler?) and wire the
  sidebar to whatever's real, or scope it down to a working non-streaming
  version rather than leave a broken chat button in the app.
- **`SearchPanel.jsx`** calls `api.searchFiles()` — a stub for AI/semantic
  search that was never built. Realistic fix: a real, simple grep/text
  search backend endpoint (search file contents across a project) is a
  small, achievable, genuinely useful replacement for the semantic search
  ambition that was never real — ship the simple version, not nothing.
- **No HITL approval UI exists anywhere in the frontend.** Several real,
  tested backend mechanisms (`delete_file`, `git_discard`, `spawn_agent`)
  route through HITL and block waiting for approval — `api.js` has
  `getPendingHitl`/`approveHitl`/`denyHitl` (Session 6) but nothing in the
  actual UI surfaces a pending request to a human. Build a real
  notification/approval component (a small panel or toast, backed by the
  real `Communications` SSE stream from Session 2, which already pushes
  `hitl.pending` events) — without this, any Worker action that needs
  approval is currently invisible in the running app.

## Stage 3 — Backend completeness pass

- Audit `execution/search.py` (Tavily-based web search) the same way
  Session 22 audited the builder and Flow — confirm it's actually wired to
  Workers' `search_web` tool correctly and works, don't assume.
- Add the still-missing binary file download endpoint (flagged since
  Session 6).
- A real look at Supabase/pgvector code paths in `memory/store.py` — never
  exercised, only SQLite has real test coverage. Either get a real test
  against them, or be explicit in docs that this is an unverified path.

## Stage 4 — Persona roster

- Continue toward the real 230+ catalog, batch by batch.
- Go back over the 85 templated (Session 22) personas' tool grants with
  real judgment, ideally informed by real usage once a live LLM is
  reachable somewhere — right now those grants were assigned from a short
  description, not a verified declared-tools list.
- Expect more routing-keyword gaps in the templated batch as real goals
  hit it; this is ongoing maintenance, not a one-time fix.

## Stage 5 — The two things this environment cannot close, ever

- A real `docker build` — no Docker daemon here.
- A real browser smoke-test — no browser here.
- A real LLM test — no network access to any provider here.

These three are structural limits of this sandbox, not deferred work items
in the normal sense. The single most valuable thing that can happen for
this whole project, at any point, is a real person running this on a real
machine with real network access.

---

## Stage 0b — Done this session (Session 23), production-readiness documentation + cleanup

- **`datetime.utcnow()` deprecation fixed across the entire Python codebase.**
  Replaced ~80 call sites in 32 files (routes, brain, governance, execution, memory,
  tests, workspace) with `datetime.now(timezone.utc)` and timezone-aware SQLAlchemy
  defaults.  No functional change; timestamps remain UTC.  Verified: no remaining
  `utcnow()` references and all Python files compile successfully.
- **P5 / P5a–P5e line-by-line security audit documented.**  Created
  [`plans/SECURITY_AUDIT.md`](plans/SECURITY_AUDIT.md) with sections for API routes,
  execution layer, governance, frontend, and docs.  Notes concrete line numbers, the
  hardened controls in place, and open residual risks (HITL UI, CSP, symlink/case
  folding, chat sanitization).
- **README.md rewritten for DevOS + current architecture.**  Dropped the stale
  “CaraiOS v4” branding, added the dual Supabase/local auth design, referenced the
  new security audit and bug-audit documents, and corrected quick-start instructions.
- **Confirmed the security fixes from prior sessions are still in place:**
  persisted `JWT_SECRET` (`core/config.py` `_get_or_create_persisted_secret`),
  child-targeted sandbox limits (`governance/sandbox.py` `_apply_ulimits`),
  thread-safe HITL singleton (`governance/hitl.py`), per-secret-salt Fernet vault
  (`governance/secrets_vault.py`), and AI-debug failure detection
  (`api/routes/scripts.py`).

Remaining priority order after this session:

1. Build a real HITL approval/toast component in the frontend (blocks safe exposure of
   destructive agent actions).
2. Remove or retire the broken `AgentPanel.jsx` shortcut (`api.streamAgent`/`reindex`
   are still stubs).
3. Port the full-stack website builder UI to trigger
   [`brain/builder.py`](brain/builder.py) through [`api/routes/extras.py`](api/routes/extras.py)
   (Stage 1 in the original plan).
4. Add CSP/Helmet headers and finish frontend hardening noted in the security audit.
5. Continue closing documented gaps in `plans/GAP_ANALYSIS.md` as resources permit.
6. Schedule a real LLM/docker/browser smoke test on a machine with network access.
