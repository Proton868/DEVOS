# Bug Audit & Fix Plan — DevOS V4

**Audit date:** 2026-07-16  
**Scope:** Entire codebase (Python backend + React frontend source)  
**Guidelines:** `record.md` (build history), `README.md` (architecture), `PRODUCTION_PLAN.md` (staged gaps)  
**Constraint:** Fix only real, confirmed bugs; maintain code stack; no scope creep beyond documented intent.

---

## Real Bugs Found (by severity)

### Critical — Security/Correctness

#### 1. `_apply_ulimits()` does not limit the child process (it limits the server itself)
- **File:** [`governance/sandbox.py`](governance/sandbox.py:243)
- **Root cause:** `resource.setrlimit()` applies to the calling process (the FastAPI server), not the child subprocess. The `proc` parameter is completely ignored. Called AFTER the subprocess already started, so child limits were never applicable anyway.
- **Real-world effect:** The sandbox provides zero resource limiting (CPU, memory, file size, FD count). The calls likely silently fail with `ValueError` (server already exceeds the low limits), so they don't crash the server — but they also don't protect anything.
- **Fix:** Replace `resource.setrlimit()` with `resource.prlimit(proc.pid, ...)` — Python 3.9+ supports setting limits by PID. This targets the child process correctly.

#### 2. `JWT_SECRET` and `SECRET_KEY` default regenerate on every restart
- **File:** [`core/config.py`](core/config.py:9) and [`core/config.py`](core/config.py:16)
- **Root cause:** `secrets.token_hex(32)` is called at module load time as the default value. Every process restart generates a new random secret, silently invalidating all existing sessions and encrypted secrets.
- **Real-world effect:** All JWTs become invalid on restart. All Fernet-encrypted secrets in the DB become undecryptable (since `secrets_vault.py` derives its key from `JWT_SECRET`). Flagged in record.md Session 6, but the default is still dangerous.
- **Fix:** Either (a) raise a hard error at startup if `JWT_SECRET` is unset (preferred — forces explicit configuration), or (b) persist the generated secret to a file on first boot and reuse it. Option (a) matches the `.env.example` guidance already shipped.

### High — Behavioral

#### 3. `BRAIN_SYSTEM_PROMPT` is missing `spawn_agent`, `graph_remember`, `graph_query`
- **File:** [`core/loop.py`](core/loop.py:100)
- **Root cause:** The system prompt lists only 6 tools (write_python, write_bash, search_web, recall_memory, mark_complete, ask_user). But `_loop()` dispatches `spawn_agent` (line 320), `graph_remember` (line 362), and `graph_query` (line 380). These dispatchers work correctly, but the Brain is never told they exist — so it will never invoke them unless the persona's own prompt includes them.
- **Real-world effect:** Workers granted `spawn_agent` or `graph_remember`/`graph_query` (product-manager, technical-writer) will never use these tools when running as the generalist Brain (not through a Worker persona). For Workers, it depends on whether `build_agent_system_prompt()` includes them — but the base prompt that personas are composed with is still incomplete.
- **Fix:** Add `spawn_agent`, `graph_remember`, `graph_query` to the tools list in `BRAIN_SYSTEM_PROMPT`, with short descriptions matching the existing tool format.

#### 4. `search_web()` creates a new `httpx.AsyncClient` per call
- **File:** [`execution/search.py`](execution/search.py:16) and [`execution/search.py`](execution/search.py:41)
- **Root cause:** Each call creates `async with httpx.AsyncClient(timeout=30)` — pays the connection-pool/SSL setup cost (~30ms) every time. This is inconsistent with the shared-client pattern established in `brain/llm.py` (Session 18).
- **Real-world effect:** ~30ms unnecessary overhead per web search call. Minor but real in a loop that might call search repeatedly.
- **Fix:** Use the shared `BrainLLM._get_http_client()` or create a module-level shared client, same singleton pattern as the rest of the codebase.

#### 5. `ai-debug` route silently treats "all providers failed" as valid fixed code
- **File:** [`api/routes/scripts.py`](api/routes/scripts.py:108)
- **Root cause:** `brain.stream_chat()` returns "All providers failed..." as a fallback string when every provider is unreachable. This string passes through the regex code extraction and is returned as `fixed_code` — the caller sees meaningless text with no indication of failure.
- **Real-world effect:** A user asking for AI debugging of a failing script gets back "All providers failed. Check your API keys..." as the "fixed code" with no error indication.
- **Fix:** Check for the fallback string and return a proper error response (HTTP 503 or an error field) instead of treating it as valid code.

### Medium — Correctness/Concurrency

#### 6. All singleton `__new__` methods have a race condition
- **Files:** [`governance/hitl.py`](governance/hitl.py:86), [`memory/graph.py`](memory/graph.py:47), [`memory/working.py`](memory/working.py), [`memory/store.py`](memory/store.py:25), [`communications/bus.py`](communications/bus.py), [`governance/ratelimit.py`](governance/ratelimit.py:81)
- **Root cause:** `if cls._instance is None: cls._instance = super().__new__(cls)` without any lock. Under concurrent uvicorn workers, two requests can both pass the `is None` check and create two instances.
- **Real-world effect:** Low probability but real — two concurrent first-requests could create two `HITLQueue` instances, meaning a HITL request submitted to one queue would never be resolvable from the other. Same for `EventBus` (events published to one instance never reach subscribers on the other).
- **Fix:** Add `import threading` and use `threading.Lock()` around the `__new__` singleton check in each class. This is a one-line change per file.

### Low — Code Quality

#### 7. `_all_providers()` swallows all exceptions
- **File:** [`brain/llm.py`](brain/llm.py:81)
- **Root cause:** `except Exception as e: logger.warning(...)` catches everything from `EndpointRegistry`, including database connection errors, schema errors, etc. These are silently downgraded to a warning log line.
- **Real-world effect:** If the database is corrupted or unreachable, custom endpoints are silently excluded with no user-visible error. The Brain falls back to built-in providers, which might work — but the real problem is invisible.
- **Fix:** Log at `logger.error` level instead of `logger.warning`, and include the exception type in the message. Optionally, surface the first such error to the caller.

#### 8. `RateLimiter.__new__` accepts `config` parameter but only uses it on first call
- **File:** [`governance/ratelimit.py`](governance/ratelimit.py:81)
- **Root cause:** `__new__` with `config` param initializes the singleton only on first call. A subsequent `RateLimiter(config=CustomConfig())` would silently ignore the custom config. Not currently triggered (all callers use no-arg `RateLimiter()`), but a latent footgun.
- **Fix:** Either remove the `config` parameter from `__new__` (since it's never used with a custom config) or add a warning when a non-None config is passed to an already-initialized singleton.

---

## Bugs NOT found (confirmed working correctly)

These were investigated and ruled out:

| Suspected issue | File | Verdict |
|---|---|---|
| SQL injection in `memory/store.py` recall | [`memory/store.py`](memory/store.py:236) | SAFE — `LIKE ?` with parameterized `%query%` pattern. The `%` are in the parameter value, not the SQL string. |
| `sub_state.decision == "complete"` type mismatch | [`core/loop.py`](core/loop.py:351) | SAFE — `LoopDecision(str, Enum)` inherits from `str`, so `LoopDecision.COMPLETE == "complete"` is `True`. |
| `HITLQueue.resolve()` sync vs async | [`governance/hitl.py`](governance/hitl.py:154) | SAFE — `asyncio.Event.set()` is thread-safe and can be called from sync code. The `asyncio.create_task()` is guarded by `try/except RuntimeError`. |
| `_embed()` creates new httpx client per call | [`memory/store.py`](memory/store.py:115) | INTENTIONAL — short-lived 10s timeout client for embeddings, different from the long-lived 120s chat client. Acceptable. |
| `secrets_vault.py` fixed salt | [`governance/secrets_vault.py`](governance/secrets_vault.py:30) | DOCUMENTED TRADEOFF — the docstring explicitly acknowledges this and explains the reasoning. Not a bug, but a known limitation. |

---

## Fix plan (execution order)

```
Phase 1 — Critical (fix first, no dependencies)
  1. Fix _apply_ulimits → use resource.prlimit(proc.pid, ...)
  2. Fix JWT_SECRET/SECRET_KEY defaults → hard error on missing config

Phase 2 — High (behavioral fixes)
  3. Add spawn_agent, graph_remember, graph_query to BRAIN_SYSTEM_PROMPT
  4. Share httpx client in execution/search.py
  5. Fix ai-debug error handling in api/routes/scripts.py

Phase 3 — Medium (concurrency hardening)
  6. Add threading.Lock to all singleton __new__ methods (6 files)

Phase 4 — Low (code quality)
  7. Upgrade _all_providers() exception logging
  8. Harden RateLimiter.__new__ config handling
```

**Total: 8 bugs across 10 files.**  
**No new dependencies required.**  
**No scope creep beyond documented intent.**  
**All fixes maintain the existing code stack and patterns.**

---

## Mermaid: Bug interconnections

```mermaid
graph TD
    A[JWT_SECRET regenerates on restart] --> B[Secrets become undecryptable]
    A --> C[All sessions invalidated]
    D[_apply_ulimits broken] --> E[No sandbox resource limits]
    F[BRAIN_SYSTEM_PROMPT missing tools] --> G[Workers never use spawn_agent/graph tools]
    F --> H[Generalist Brain never delegates or queries graph]
    I[search_web creates new client per call] --> J[~30ms overhead per search]
    K[ai-debug error handling] --> L[Silently returns garbage as fixed code]
    M[Singleton race conditions] --> N[Duplicate HITLQueue/EventBus possible]