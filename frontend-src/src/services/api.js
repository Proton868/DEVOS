// DevOS frontend -> DevOS v3 backend.
//
// Rewired per the Agency OS consolidation decision (Master Plan §"Immediate
// Next Actions" / record.md Session 1): DevOS's own Node backend is retired.
// This file now talks to DevOS v3's FastAPI routes instead.
//
// Two real architecture differences from the original api.js, both handled
// explicitly below rather than papered over:
//   1. Auth: DevOS is dual-auth (security-audit P2, "Supabase-primary with
//      local fallback"). Its own local JWT (username/password -> bcrypt ->
//      HS256 JWT) is kept in localStorage as before; a Supabase access
//      token (managed by the Supabase SDK in supabase.js, only present if
//      REACT_APP_SUPABASE_URL/REACT_APP_SUPABASE_ANON_KEY are configured at
//      build time) is used as a fallback when no local token is present.
//      See resolveAuthToken()/syncSupabaseSession() below and
//      api/routes/auth.py's module docstring for the full design.
//   2. Projects: DevOS is project-scoped (every file/git/terminal route
//      is under /api/{files,vcs,terminal}/{project_id}/...) but DevOS's
//      original calls had no project concept at all (one flat workspace).
//      This file introduces a lightweight "current project" resolver
//      (getCurrentProject/setCurrentProject) defaulting to a fixed id, so
//      every existing call site in the components keeps working with zero
//      changes, while the underlying backend is correctly multi-project.
//
// Honesty note: not every original method has a real DevOS backend yet.
// Methods under "NOT YET IMPLEMENTED" throw a clear, typed error instead of
// silently succeeding or returning fake data — see record.md Session 6 for
// the full list and why each one isn't done.

// Empty string = same-origin relative requests. This is the correct
// default now that app.py serves this build directly (see record.md
// Session 19) — frontend and backend are literally the same process on
// the same origin, so there's no separate host/port to hardcode. Only set
// REACT_APP_DEVOS_URL if you're running the frontend dev server
// separately from the backend (e.g. `npm start` against a backend running
// elsewhere during development).
const BASE = process.env.REACT_APP_DEVOS_URL || "";
const TOKEN_KEY = "devos_token";
const PROJECT_KEY = "devos_current_project";
const DEFAULT_PROJECT = "default";

export function baseUrl() { return BASE; }

export async function supabaseExchange(accessToken) {
  const r = await fetch(`${BASE}/api/auth/supabase/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: accessToken }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `Token exchange failed: HTTP ${r.status}`);
  }
  const data = await r.json();
  if (data?.token) setToken(data.token);
  return data;
}

// ── Auth ──────────────────────────────────────────────────────
// security-audit P2d: dual-auth token resolution. DevOS's own local JWT
// (username/password -> bcrypt -> HS256 JWT, stored in localStorage under
// TOKEN_KEY) and a Supabase access token (session managed by the Supabase
// SDK itself in supabase.js, via localStorage under its own "devos-auth"
// key) can both be in play — see api/routes/auth.py's module docstring for
// the full "Supabase-primary with local fallback" design this mirrors.
export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// Resolves whichever token should be sent as the Authorization header for
// the next request: a local token takes priority if present (it's cheaper
// to use than every request awaiting Supabase's session lookup, and a
// locally-authenticated user has no Supabase session anyway); otherwise
// falls back to the current Supabase session's access token, if any.
async function resolveAuthToken() {
  const local = getToken();
  if (local) return local;
  try {
    const { getToken: getSupabaseToken } = await import("./supabase");
    return await getSupabaseToken();
  } catch {
    return null;
  }
}

export async function login(username, password) {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `Login failed: HTTP ${r.status}`);
  }
  const data = await r.json();
  setToken(data.token);
  return data.user;
}

// Called right after a successful supabase.auth.signInWithPassword() (or on
// session restore) — POSTs the fresh Supabase access token to the backend's
// /api/auth/supabase/sync so a local User row exists before anything else
// runs (security-audit P2c/P2f). Returns the same user shape login() does,
// plus `supabase_linked: true`.
export async function syncSupabaseSession() {
  const { getToken: getSupabaseToken } = await import("./supabase");
  const token = await getSupabaseToken();
  if (!token) throw new Error("No active Supabase session");
  const r = await fetch(`${BASE}/api/auth/supabase/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `Supabase sync failed: HTTP ${r.status}`);
  }
  const data = await r.json();
  // If the backend issued a local token, persist it so resolveAuthToken()
  // prefers it on subsequent calls.
  if (data?.token) setToken(data.token);
  return data;
}

export async function logout() {
  setToken(null);
  try {
    const { supabase } = await import("./supabase");
    if (supabase) await supabase.auth.signOut();
  } catch {}
}

export async function verifySession() {
  // Validates a stored token against the backend rather than trusting its
  // mere presence in localStorage — a token can be stale (server restarted
  // with a fresh JWT_SECRET, per record.md Session 6's known follow-up) or
  // expired. Checks BOTH possible sources (local token or an active
  // Supabase session — security-audit P2e) before giving up. Returns the
  // user object if valid, null otherwise (and clears the dead local token
  // so the app doesn't keep retrying it; a dead Supabase session is left
  // for the Supabase SDK's own refresh/expiry handling).
  const token = await resolveAuthToken();
  if (!token) return null;
  try {
    return await req(`/api/auth/me`);
  } catch (e) {
    setToken(null);
    return null;
  }
}

// ── Current project ───────────────────────────────────────────
export function getCurrentProject() {
  return localStorage.getItem(PROJECT_KEY) || DEFAULT_PROJECT;
}

export function setCurrentProject(projectId) {
  localStorage.setItem(PROJECT_KEY, projectId);
}

// ── Low-level request helpers ────────────────────────────────
async function req(path, opts = {}) {
  const token = await resolveAuthToken();
  const r = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ error: r.statusText }));
    const message = err.detail
      ? (typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail))
      : (err.error || err.message || `HTTP ${r.status}`);
    throw new Error(message);
  }
  // 204/empty-body responses (e.g. some DELETE calls) won't have JSON
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

function notImplemented(name, reason) {
  return () => {
    throw new Error(
      `api.${name}() is not implemented against DevOS v3 yet. ${reason} ` +
      `See record.md Session 6 "Known follow-ups" for the full list.`
    );
  };
}

// ── Files ─────────────────────────────────────────────────────
// DevOS's delete route blocks on a HITL approval (see governance/hitl.py) —
// it will not resolve until someone calls api.approveHitl(id) or it times
// out at 120s server-side. There's no approval-modal UI wired up to this
// yet (flagged in record.md); for now, a caller of deleteFile/discard needs
// to also be watching subscribeToEvents() for a "hitl.pending" event and
// call approveHitl/denyHitl itself, or the call will simply hang until the
// timeout. This is documented rather than hidden behind a fake instant
// success, since a UI that assumes instant deletion would be actively
// misleading here.
const filesApi = {
  getTree:    () => req(`/api/files/${getCurrentProject()}/tree`),
  readFile:   (path) => req(`/api/files/${getCurrentProject()}/read?path=${encodeURIComponent(path)}`),
  writeFile:  (path, content) => req(`/api/files/${getCurrentProject()}/write`, { method: "POST", body: JSON.stringify({ path, content }) }),
  createFile: (path, type = "file") => req(`/api/files/${getCurrentProject()}/create`, { method: "POST", body: JSON.stringify({ path, is_dir: type === "dir" }) }),
  renameFile: (oldPath, newPath) => req(`/api/files/${getCurrentProject()}/rename`, { method: "POST", body: JSON.stringify({ path: oldPath, new_path: newPath }) }),
  deleteFile: (path) => req(`/api/files/${getCurrentProject()}/delete?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
  downloadUrl: (path) => {
    return `${BASE}/api/files/${getCurrentProject()}/download?path=${encodeURIComponent(path)}`;
  },
};

const searchApi = {
  searchFiles: (query, max_results = 20) =>
    req(`/api/search/files`, {
      method: "POST",
      body: JSON.stringify({ query, project_id: getCurrentProject(), max_results }),
    }),
  getIndexStatus: () =>
    req(`/api/search/index/status?project_id=${encodeURIComponent(getCurrentProject())}`),
  reindex: () =>
    req(`/api/search/index/reindex?project_id=${encodeURIComponent(getCurrentProject())}`, { method: "POST" }),
};

const builderApi = {
  listStacks: () => req(`/api/extras/stacks`),
  buildProject: (spec) => req(`/api/extras/build`, { method: "POST", body: JSON.stringify(spec) }),
  listProjects: () => req(`/api/extras/projects`),
  getProjectFiles: (projectId) => req(`/api/extras/projects/${encodeURIComponent(projectId)}/files`),
  // BuildResult.to_dict() does not include per-file content; fetch them through
  // the files API once the generated project is the current project.
  getBuildStatus: (projectId) => req(`/api/extras/projects/${encodeURIComponent(projectId)}/files`),
};

// ── Git ───────────────────────────────────────────────────────
const gitApi = {
  gitStatus:   () => req(`/api/vcs/${getCurrentProject()}/status`),
  gitInit:     () => req(`/api/vcs/${getCurrentProject()}/init`, { method: "POST" }),
  gitStage:    (files) => req(`/api/vcs/${getCurrentProject()}/stage`, { method: "POST", body: JSON.stringify({ paths: files }) }),
  gitUnstage:  (files) => req(`/api/vcs/${getCurrentProject()}/unstage`, { method: "POST", body: JSON.stringify({ paths: files }) }),
  gitCommit:   (message) => req(`/api/vcs/${getCurrentProject()}/commit`, { method: "POST", body: JSON.stringify({ message }) }),
  gitPush:     (remote, branch) => req(`/api/vcs/${getCurrentProject()}/push`, { method: "POST", body: JSON.stringify({ remote: remote || "origin", branch }) }),
  gitPull:     (remote, branch) => req(`/api/vcs/${getCurrentProject()}/pull`, { method: "POST", body: JSON.stringify({ remote: remote || "origin", branch }) }),
  gitCheckout: (branch, create) => req(`/api/vcs/${getCurrentProject()}/checkout`, { method: "POST", body: JSON.stringify({ branch, create: !!create }) }),
  gitDiscard:  (path) => req(`/api/vcs/${getCurrentProject()}/discard`, { method: "POST", body: JSON.stringify({ path }) }),
  gitDiff:     (path, staged = false) => req(`/api/vcs/${getCurrentProject()}/diff${path ? `?path=${encodeURIComponent(path)}&staged=${staged}` : `?staged=${staged}`}`),
  // gitAddRemote: called by a component but was never in the original
  // api.js either (a pre-existing gap in DevOS itself, not introduced by
  // this rewiring) — and DevOS's GitService has no add-remote wrapper
  // yet. Real gap on both sides; flagged rather than silently stubbed.
  gitAddRemote: notImplemented("gitAddRemote",
    "Neither the original DevOS backend nor DevOS's GitService implement this yet."),
};

// ── Terminal (new — DevOS never had this in api.js since its own
//    node-pty-backed terminal was a separate, non-api.js code path) ──
const terminalApi = {
  runCommand: (command, timeout = 60) =>
    req(`/api/terminal/${getCurrentProject()}/run`, { method: "POST", body: JSON.stringify({ command, timeout }) }),
  // Streaming terminal is a WebSocket, not a fetch — exposed as a factory
  // that returns a ready-to-use WebSocket rather than an async function,
  // since the calling component needs the raw socket to attach handlers.
  // WebSocket needs an explicit ws:// or wss:// scheme -- unlike fetch,
  // it can't resolve a bare relative path against the page's origin with
  // the right scheme automatically. When BASE is empty (same-origin,
  // the normal case now), derive the ws/wss + host from window.location
  // instead of naively string-replacing an empty BASE (which would
  // produce an invalid, scheme-less URL and throw.
  openStreamingTerminal: () => {
    const wsBase = BASE
      ? BASE.replace(/^http/, "ws")
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
    return new WebSocket(`${wsBase}/api/terminal/${getCurrentProject()}/ws`);
  },
};

const chatApi = {
  // Accepts the rich shape ChatSidebar sends ({ providerId, model, messages,
  // system, activeFile, mentionedFiles, useCodebaseContext }) and translates
  // it to the backend's flat /api/chat/send contract ({ message, session_id,
  // provider, model, system_prompt }). The backend's /send route doesn't yet
  // accept a messages array or context fields, so we take the last user
  // message from `messages` (or fall back to `message`) and pass `system` as
  // `system_prompt`. The richer context (activeFile, mentionedFiles,
  // useCodebaseContext) is intentionally dropped here rather than silently
  // breaking — the backend has no route that accepts them yet.
  async *streamChat({
    providerId,
    model,
    message,
    messages,
    session_id,
    system,
    system_prompt,
  }) {
    const token = getToken();
    const finalMessage =
      message ||
      (Array.isArray(messages) && messages.length
        ? [...messages].reverse().find((m) => m.role === "user")?.content
        : undefined);
    if (!finalMessage) {
      yield { error: "No message to send." };
      return;
    }
    const body = {
      message: finalMessage,
      session_id,
      provider: providerId,
      model,
      system_prompt: system_prompt || system,
    };
    const r = await fetch(`${BASE}/api/chat/send`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });
    if (!r.ok || !r.body) throw new Error(`Chat stream failed: HTTP ${r.status}`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const evt of events) {
        const line = evt.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const payload = JSON.parse(line.slice("data: ".length));
        if (payload.error) yield { error: payload.error, session_id: payload.session_id };
        else if (payload.delta) yield { text: payload.delta, session_id: payload.session_id };
        else yield payload;
      }
    }
  },
};

// ── Governance / UCIP ─────────────────────────────────────────
const governanceApi = {
  ucipHealth: () => req(`/api/governance/metrics`),
  // ^ DevOS has no single "/ucip/health" endpoint the way DevOS's own
  // UCIP implementation did — /governance/metrics is the closest real
  // equivalent (HITL stats + audit summary). Not a 1:1 match; flagged.
  ucipTraces: (params = {}) => req(`/api/governance/traces?${new URLSearchParams(params)}`),
  ucipCapabilities: (trustLevel = "OPERATOR") => req(`/api/governance/ucip/capabilities/${trustLevel}`),
  ucipIdentity: (sessionId) => req(`/api/governance/ucip/identity?session_id=${encodeURIComponent(sessionId)}`),
  getPendingHitl: () => req(`/api/governance/hitl/pending`),
  approveHitl: (requestId) => req(`/api/governance/hitl/${requestId}/approve`, { method: "POST" }),
  denyHitl: (requestId) => req(`/api/governance/hitl/${requestId}/deny`, { method: "POST" }),
};

// ── Communications (new — live event stream, see communications/bus.py) ──
// The backend sends *named* SSE events (event: hitl.pending, event:
// hitl.resolved, ...), not generic unnamed "message" frames — that's the
// correct, standard way to do typed SSE. A naive `es.onmessage = ...`
// listener (which is what the first version of this function used) never
// fires for named events at all, so it silently received nothing. Fixed by
// registering listeners for every currently-known event type, while also
// returning the raw EventSource so a caller can addEventListener() for a
// new type this file doesn't know about yet without needing another edit
// here.
const KNOWN_EVENT_TYPES = ["hitl.pending", "hitl.resolved"];

function subscribeToEvents(onEvent, onError) {
  const token = getToken();
  const es = new EventSource(`${BASE}/api/comms/stream?token=${encodeURIComponent(token || "")}`);
  const handler = (type) => (e) => onEvent({ type, data: JSON.parse(e.data) });
  for (const type of KNOWN_EVENT_TYPES) {
    es.addEventListener(type, handler(type));
  }
  es.onerror = (e) => { if (onError) onError(e); };
  const unsubscribe = () => es.close();
  unsubscribe.eventSource = es; // escape hatch for unlisted event types
  return unsubscribe;
}

// ── Flow (PyRunner-style scheduled scripts) ────────────────────
const flowApi = {
  flowScripts: () => req(`/api/scripts`),
  flowScript: (id) => req(`/api/scripts/${id}`),
  runInfos: async (id) => req(`/api/scripts/${id}/runs?limit=20`),
  createFlowScript: (script) => req(`/api/scripts`, { method: "POST", body: JSON.stringify(script) }),
  updateFlowScript: (id, patch) => req(`/api/scripts/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteFlowScript: (id) => req(`/api/scripts/${id}`, { method: "DELETE" }),
  runFlowScript: (id) => req(`/api/scripts/${id}/run`, { method: "POST" }),
  flowScriptRuns: (id, limit = 10) => req(`/api/scripts/${id}/runs?limit=${limit}`),
  flowScriptAiDebug: (id) => req(`/api/scripts/${id}/ai-debug`, { method: "POST" }),
  // DevOS has no single "/flow/stats" aggregate endpoint — computed
  // client-side here from the scripts list instead. Fixed a real bug
  // (Session 22): this used to filter by `s.enabled`, a field the real
  // backend doesn't have -- the actual field is `is_active`.
  flowStats: async () => {
    const list = await req(`/api/scripts`);
    return { total: list.length, enabled: list.filter((s) => s.is_active).length };
  },
  // Secrets (Session 22) -- the real gap FlowPanel's old, broken separate
  // client expected but the backend never had until now.
  listSecrets: () => req(`/api/secrets`),
  createSecret: (name, value, description) =>
    req(`/api/secrets`, { method: "POST", body: JSON.stringify({ name, value, description }) }),
  deleteSecret: (id) => req(`/api/secrets/${id}`, { method: "DELETE" }),
  // Webhook triggers (G7) -- fire a script from an external HTTP call using
  // its per-script webhook_token, no auth needed for the trigger itself.
  webhookUrl: (token) => `${BASE}/api/scripts/webhook/${token}`,
  rotateWebhookToken: (id) => req(`/api/scripts/${id}/webhook/rotate`, { method: "POST" }),
  // Script chaining (G8) -- run a child script automatically after a
  // parent finishes, gated on success/failure.
  listChains: () => req(`/api/scripts/chains/all`),
  createChain: (parent_script_id, child_script_id, condition = "on_success") =>
    req(`/api/scripts/chains`, { method: "POST", body: JSON.stringify({ parent_script_id, child_script_id, condition }) }),
  toggleChain: (chainId) => req(`/api/scripts/chains/${chainId}`, { method: "PATCH" }),
  deleteChain: (chainId) => req(`/api/scripts/chains/${chainId}`, { method: "DELETE" }),
};

// ── Workers (new — Stage 3, Sessions 8-10: AGENT_LIBRARY personas are now
//    real, invocable, properly-delegated Workers, not just data) ──────────
const workersApi = {
  listWorkers: () => req(`/api/workers`),
  getWorker: (slug) => req(`/api/workers/${encodeURIComponent(slug)}`),
  runWorkerSync: (slug, goal, opts = {}) =>
    req(`/api/workers/${encodeURIComponent(slug)}/run/sync`, {
      method: "POST",
      body: JSON.stringify({ goal, ...opts }),
    }),
  // Streaming variant returns an EventSource-like consumable via fetch's
  // ReadableStream, since this is a POST (EventSource only supports GET).
  // Caller drives iteration; this just wraps the parsing.
  async *streamWorker(slug, goal, opts = {}) {
    const token = getToken();
    const r = await fetch(`${BASE}/api/workers/${encodeURIComponent(slug)}/run`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ goal, ...opts }),
    });
    if (!r.ok || !r.body) throw new Error(`Worker stream failed: HTTP ${r.status}`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const evt of events) {
        const line = evt.split("\n").find((l) => l.startsWith("data: "));
        if (line) yield JSON.parse(line.slice("data: ".length));
      }
    }
  },
  // Multi-worker coordination (Session 9): decomposes the goal and
  // dispatches each subtask to whichever Worker persona fits best,
  // running independent subtasks concurrently.
  runCoordinatedPlan: (goal, opts = {}) =>
    req(`/api/workers/plan/run`, { method: "POST", body: JSON.stringify({ goal, ...opts }) }),
  async *streamAgent({ providerId, model, task, session_id, trust_level = "OPERATOR" }) {
    const slug = "fullstack-engineer";
    for await (const evt of workersApi.streamWorker(slug, task, {
      provider: providerId,
      model,
      session_id,
      trust_level,
    })) {
      if (evt.type === "step") {
        yield { type: "thinking", text: `${evt.step_type}: ${evt.content}`, meta: evt.meta };
      } else if (evt.type === "done") {
        const text = evt.final_answer || evt.output || evt.content || "Done";
        yield { type: "answer", text, ...evt };
        yield { type: "done", ...evt };
      } else if (evt.type === "error") {
        yield { type: "error", message: evt.content || "Worker error" };
      } else {
        yield evt;
      }
    }
  },
};

// ── AI / Agent / Composer / Extensions — NOT YET IMPLEMENTED ───
// These map to Stage 2/3 (Cognitive System, Workers) of the Master Plan,
// which haven't been built yet. Listed explicitly, each throwing a clear
// error naming what's missing, rather than silently returning empty
// results that would look like "no data" instead of "not built."
const notBuiltYet = {
  getProviders: () => req(`/api/models`), // this part IS real — provider list works
};

// ── AI code intelligence (completion, inline edit, explain) ─────────────
// Shared low-level SSE consumer for the /api/chat/edit and /api/chat/explain
// streaming endpoints -- both follow the exact same {text}/{error} event
// shape as chatApi.streamChat above, just against different backend routes.
async function* _streamSSE(path, body) {
  const token = getToken();
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) throw new Error(`Stream failed: HTTP ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop();
    for (const evt of events) {
      const line = evt.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      yield JSON.parse(line.slice("data: ".length));
    }
  }
}

const aiApi = {
  complete: ({ providerId, model, prefix, suffix, language, filepath }) =>
    req(`/api/models/complete`, {
      method: "POST",
      body: JSON.stringify({ providerId, model, prefix, suffix, language, filepath }),
    }),
  streamEdit: ({ providerId, model, instruction, selectedCode, fullFile, language }) =>
    _streamSSE(`/api/chat/edit`, { providerId, model, instruction, selectedCode, fullFile, language }),
  streamExplain: ({ providerId, model, code, language, filepath }) =>
    _streamSSE(`/api/chat/explain`, { providerId, model, code, language, filepath }),
  composerPlan: ({ providerId, model, instruction, activeFile }) =>
    req(`/api/composer/plan`, {
      method: "POST",
      body: JSON.stringify({ providerId, model, instruction, activeFile, projectId: getCurrentProject() }),
    }),
  async *streamComposerExecute({ providerId, model, instruction, plan, activeFile }) {
    yield* _streamSSE(`/api/composer/execute`, { providerId, model, instruction, plan, activeFile, projectId: getCurrentProject() });
  },
  composerApply: (changes) =>
    req(`/api/composer/apply`, { method: "POST", body: JSON.stringify({ changes, projectId: getCurrentProject() }) }),
};

// ── Research (Deep Research Agent) ──────────────────────────────
const researchApi = {
  startResearch: (question, opts = {}) =>
    req("/api/research/start", { method: "POST", body: JSON.stringify({ question, ...opts }) }),
  getResearchJob: (jobId) =>
    req(`/api/research/jobs/${jobId}`),
  listResearchJobs: () =>
    req("/api/research/jobs"),
  quickResearch: (question, opts = {}) =>
    req("/api/research/quick", { method: "POST", body: JSON.stringify({ question, ...opts }) }),
};

// ── Workflows ──────────────────────────────────────────────────
const workflowApi = {
  listWorkflows: (params = {}) =>
    req(`/api/workflows?${new URLSearchParams(params)}`),
  getWorkflow: (id) =>
    req(`/api/workflows/${id}`),
  createWorkflow: (data) =>
    req("/api/workflows", { method: "POST", body: JSON.stringify(data) }),
  updateWorkflow: (id, data) =>
    req(`/api/workflows/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteWorkflow: (id) =>
    req(`/api/workflows/${id}`, { method: "DELETE" }),
  exportWorkflow: (id, format = "yaml") =>
    req(`/api/workflows/${id}/export?format=${format}`),
  importWorkflow: (content, format = "yaml", name) =>
    req("/api/workflows/import", { method: "POST", body: JSON.stringify({ format, content, name }) }),
  getUcipPlan: (id) =>
    req(`/api/workflows/${id}/ucip`),
};

// ── Capabilities ────────────────────────────────────────────────
const capabilitiesApi = {
  listCapabilities: (params = {}) =>
    req(`/api/capabilities?${new URLSearchParams(params)}`),
  getCapability: (slug) =>
    req(`/api/capabilities/${slug}`),
  listCategories: () =>
    req("/api/capabilities/categories"),
};

// ── Evidence ────────────────────────────────────────────────────
const evidenceApi = {
  listChains: (limit = 50) =>
    req(`/api/evidence/chains?limit=${limit}`),
  getChain: (chainId) =>
    req(`/api/evidence/chains/${chainId}`),
  replayChain: (chainId) =>
    req(`/api/evidence/chains/${chainId}/replay`),
  chainStats: (chainId) =>
    req(`/api/evidence/chains/${chainId}/stats`),
};

// ── Ponytail Pipeline ───────────────────────────────────────────
const ponytailApi = {
  getStages: () =>
    req("/api/ponytail/stages"),
  runPipeline: (goal, codeContext, opts = {}) =>
    req("/api/ponytail/run", { method: "POST", body: JSON.stringify({ goal, code_context: codeContext, ...opts }) }),
};

// ── MCP ─────────────────────────────────────────────────────────
const mcpApi = {
  listMcpPresets: () =>
    req("/api/mcp/presets"),
  listServers: () =>
    req("/api/mcp/servers"),
  connectServer: (name, command) =>
    req("/api/mcp/connect", { method: "POST", body: JSON.stringify({ name, command }) }),
  disconnectServer: (name) =>
    req(`/api/mcp/disconnect/${name}`, { method: "POST" }),
  listTools: () =>
    req("/api/mcp/tools"),
  callTool: (name, args) =>
    req("/api/mcp/call", { method: "POST", body: JSON.stringify({ name, arguments: args }) }),
};

// ── Marketplace (npm/PyPI search + curated automation templates) ──
const marketplaceApi = {
  listAutomationTemplates: (category) =>
    req(`/api/marketplace/templates${category ? `?category=${encodeURIComponent(category)}` : ""}`),
  listTemplateCategories: () =>
    req("/api/marketplace/templates/categories"),
  getAutomationTemplate: (id) =>
    req(`/api/marketplace/templates/${id}`),
  searchPackages: (q, registry = "npm") =>
    req(`/api/marketplace/search?q=${encodeURIComponent(q)}&registry=${registry}`),
  installPackages: (scriptId, packages) =>
    req("/api/marketplace/install", { method: "POST", body: JSON.stringify({ script_id: scriptId, packages }) }),
};

// ── Editable provider/model settings ──────────────────────────────
const providerConfigApi = {
  getProviderConfig: () =>
    req("/api/models/providers/config"),
  saveProviderConfig: (updates) =>
    req("/api/models/providers/config", { method: "PUT", body: JSON.stringify(updates) }),
  testProviderConnection: (provider) =>
    req("/api/models/providers/test", { method: "POST", body: JSON.stringify({ provider }) }),
};

// ── IMPORTANT: notBuiltYet is spread FIRST so it only fills in for methods
// that don't have a real implementation. Previously, it was spread LAST,
// which meant its stubs SILENTLY OVERRODE the real streamAgent, streamChat,
// searchFiles, getIndexStatus, reindex, and getProviders — causing every
// component that called those methods to throw "not implemented" errors
// despite the real implementations existing in the file.
export const api = {
  health: () => req("/api/health"),
  getSettings: () => req("/api/models/settings"),
  supabaseExchange,
  baseUrl,
  ...notBuiltYet,
  ...aiApi,
  ...filesApi,
  ...gitApi,
  ...terminalApi,
  ...governanceApi,
  ...flowApi,
  ...workersApi,
  ...searchApi,
  ...builderApi,
  ...chatApi,
  ...researchApi,
  ...workflowApi,
  ...capabilitiesApi,
  ...evidenceApi,
  ...ponytailApi,
  ...mcpApi,
  ...marketplaceApi,
  ...providerConfigApi,
  // ── AI OS extensions (Stage 3-6 frontend) ─────────────────
  // These gracefully degrade: if the backend route doesn't exist yet,
  // the component catches the error and shows empty state.
  runTerminalCommand: (cmd) =>
    req(`/api/terminal/${getCurrentProject()}/run`, {
      method: "POST",
      body: JSON.stringify({ command: cmd }),
    }).catch((e) => { throw e; }),
  getMemory: (params = {}) =>
    req(`/api/memory/search`, {
      method: "POST",
      body: JSON.stringify({ ...params }),
    }).catch(() => ({ items: [], memories: [] })),
  updateMemory: (id, patch) =>
    req(`/api/memory/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }).catch(() => null),
  deleteMemory: (id) =>
    req(`/api/memory/${id}`, { method: "DELETE" }).catch(() => null),
  getWorkers: () =>
    req(`/api/workers`).catch(() => ({ agents: [], workers: [] })),
  debugContinue: () => req(`/api/debug/continue`, { method: "POST" }).catch(() => null),
  debugPause: () => req(`/api/debug/pause`, { method: "POST" }).catch(() => null),
  debugStop: () => req(`/api/debug/stop`, { method: "POST" }).catch(() => null),
  debugStep: () => req(`/api/debug/step`, { method: "POST" }).catch(() => null),
};

export { subscribeToEvents };

export function getLanguageFromPath(filePath) {
  const ext = filePath?.split(".").pop()?.toLowerCase();
  const map = {
    js:"javascript", jsx:"javascript", ts:"typescript", tsx:"typescript",
    py:"python", rb:"ruby", go:"go", rs:"rust", java:"java",
    cpp:"cpp", c:"c", cs:"csharp", php:"php", swift:"swift", kt:"kotlin",
    html:"html", css:"css", scss:"scss", less:"less",
    json:"json", yaml:"yaml", yml:"yaml", toml:"toml", md:"markdown",
    xml:"xml", sql:"sql", sh:"shell", bash:"shell", dockerfile:"dockerfile",
    tf:"terraform", env:"plaintext", txt:"plaintext",
  };
  return map[ext] || "plaintext";
}
