"""DevOS v3 — Brain + Execution + Governance + Agency Agents + AIS-OS Workspace"""
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from core.config import settings
from core.database import init_db


class _JSONLogFormatter(logging.Formatter):
    """Emits one JSON object per log line (security-audit P4b). Deliberately
    minimal/dependency-free rather than pulling in python-json-logger --
    production log aggregators (ELK, Loki, CloudWatch, Datadog, etc.) all
    consume plain newline-delimited JSON just fine. `request_id` is included
    when the log call supplies one via `extra={"request_id": ...}` (see
    ObservabilityMiddleware/RequestIDMiddleware), so log lines for a single
    HTTP request can be correlated even in json mode."""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _configure_logging():
    """Switch between human-readable text logs (default, best for local dev
    and `docker logs`/`journalctl` tailing) and structured JSON logs (set
    LOG_FORMAT=json in .env for production log aggregation) — security-audit
    P4b. Both formats go to stdout/stderr via the standard logging handler,
    so this is a drop-in swap with no other code changes required."""
    handler = logging.StreamHandler()
    if settings.LOG_FORMAT.lower() == "json":
        handler.setFormatter(_JSONLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]


_configure_logging()
logger = logging.getLogger("devos")


# security-audit P3d: there is no self-service password-set/change endpoint
# anywhere in this codebase (only the bootstrap admin account, created below
# in _create_admin(), and Supabase-synced accounts which never get a local
# password at all — see api/routes/auth.py's sync_supabase_user()). So
# "password policy" has exactly one real enforcement point: warning loudly
# when the one password an operator does configure (ADMIN_PASSWORD) is weak,
# since there's nothing else to validate against. If a password-change
# endpoint is ever added, this same check should be reused there.
_COMMON_WEAK_PASSWORDS = {
    "123456", "password", "admin", "12345678", "qwerty", "letmein",
    "changeme", "admin123", "welcome", "111111", "123456789", "password1",
}


def _is_weak_password(pw: str) -> Optional[str]:
    """Returns a human-readable reason if `pw` fails a minimal strength bar,
    or None if it passes. Deliberately simple (length + common-password
    denylist) rather than a complex entropy calculator — good enough to
    catch the "123456" class of mistake without adding a dependency."""
    if not pw:
        return "empty"
    if len(pw) < 8:
        return "shorter than 8 characters"
    if pw.lower() in _COMMON_WEAK_PASSWORDS:
        return "a well-known default/common password"
    return None


def _validate_startup_env():
    """Fail fast (or loudly warn) on dangerous misconfiguration before the
    app starts serving traffic (security-audit P4c). Deliberately does not
    raise for most issues -- this app is also meant to run "out of the box"
    for local/dev use -- but DEBUG=True combined with a non-localhost
    ALLOWED_ORIGINS, or a production-looking origin list with no
    JWT_SECRET/ADMIN_PASSWORD set, are logged loudly since they're the
    kind of thing that's easy to miss in a .env file."""
    if settings.DEBUG:
        logger.warning("[startup] DEBUG=True — do not run this in production (cookies are sent over plain HTTP, stack traces may leak).")
    if not settings.JWT_SECRET:
        logger.warning("[startup] JWT_SECRET is empty — a persisted random secret was generated (see core/.devos_secret). Set JWT_SECRET explicitly in production so it doesn't depend on that file surviving.")
    if settings.has_supabase and not (settings.SUPABASE_URL.startswith("https://")):
        logger.warning("[startup] SUPABASE_URL does not start with https:// — Supabase JWKS verification requires TLS.")
    if any(o in ("*",) for o in settings.ALLOWED_ORIGINS):
        logger.warning("[startup] ALLOWED_ORIGINS contains '*' — combined with allow_credentials=True this is rejected by browsers and is almost never what you want; set explicit origins.")
    # P6h: the shipped default is localhost-only so the app still runs
    # out-of-the-box for local dev, but that same default silently
    # breaks (or worse, insecurely wildcards) CORS if it's ever left
    # unset in a real deployment. Warn loudly whenever it looks like
    # we're not running locally (DEBUG=False, i.e. a production-style
    # config) but the default was never overridden in .env.
    if not settings.DEBUG and settings.ALLOWED_ORIGINS == ["http://localhost:8000"]:
        logger.warning("[startup] ALLOWED_ORIGINS is still the localhost-only default while DEBUG=False — "
                        "set ALLOWED_ORIGINS in .env to your real deployed origin(s) or the frontend will be blocked by CORS.")
    # P3d: warn (don't block startup — this app must still work out of the
    # box) if the configured admin password is weak. If ADMIN_PASSWORD is
    # unset entirely, _create_admin() below generates a strong random one
    # instead, so there's nothing to warn about in that case.
    if settings.ADMIN_PASSWORD:
        weak_reason = _is_weak_password(settings.ADMIN_PASSWORD)
        if weak_reason:
            logger.warning("[startup] ADMIN_PASSWORD is weak (%s) — set a strong, random ADMIN_PASSWORD "
                            "in .env before running in production, or leave it unset to auto-generate one.",
                            weak_reason)
    logger.info("[startup] AUTH_ENABLED=%s has_supabase=%s ALLOWED_ORIGINS=%s",
                settings.AUTH_ENABLED, settings.has_supabase, settings.ALLOWED_ORIGINS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 DevOS v3 starting...")
    _validate_startup_env()
    await init_db()
    await _create_admin()
    await _init_memory()
    await _start_scheduler()
    from governance.checkpoint import CheckpointManager
    CheckpointManager().cleanup_old(48)
    logger.info("✅ DevOS v3 ready → http://localhost:8000")
    yield
    # Graceful shutdown (security-audit P4d) -- best-effort, each step
    # independently guarded so one failing cleanup step doesn't prevent the
    # others from running during shutdown.
    logger.info("🛑 DevOS v3 shutting down...")
    try:
        from api.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as e:
        logger.warning(f"[shutdown] scheduler stop: {e}")
    try:
        from governance.audit import AuditLogger
        AuditLogger().close()
    except Exception as e:
        logger.warning(f"[shutdown] audit logger close: {e}")
    try:
        from execution.pty_session import kill_all_sessions
        await kill_all_sessions()
    except Exception as e:
        logger.warning(f"[shutdown] PTY sessions kill: {e}")
    try:
        from core.database import engine
        await engine.dispose()
    except Exception as e:
        logger.warning(f"[shutdown] db engine dispose: {e}")
    logger.info("👋 DevOS v3 shutdown complete")

async def _create_admin():
    import secrets as _s
    from core.database import AsyncSessionLocal, User
    from sqlalchemy import select
    import bcrypt
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.is_admin==True).limit(1))
        if r.scalar_one_or_none(): return
        pw = settings.ADMIN_PASSWORD or _s.token_urlsafe(12)
        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        db.add(User(username=settings.ADMIN_USER, email=settings.ADMIN_EMAIL, hashed_password=hashed, is_admin=True))
        await db.commit()
        if not settings.ADMIN_PASSWORD:
            print(f"\n{'═'*52}\n  Admin: {settings.ADMIN_USER} / {pw}\n{'═'*52}\n")

async def _init_memory():
    from memory.store import MemoryStore
    await MemoryStore().init()

async def _start_scheduler():
    try:
        from api.scheduler import start_scheduler
        await start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler: {e}")

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response per OWASP best practices."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # CSP: 'unsafe-inline'/'unsafe-eval' for scripts removed (security-audit
        # P3c) -- the React build (frontend-src) doesn't rely on eval() or
        # inline <script> blocks, and Monaco's web workers are loaded from
        # 'self' blob: URLs which are already covered by worker-src below.
        # style-src keeps 'unsafe-inline' because Monaco and several React
        # components set inline style attributes at runtime (acceptable
        # residual risk -- style-only injection can't execute script).
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "worker-src 'self' blob:; "
            "connect-src 'self' ws: wss: https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Cross-origin isolation headers (security-audit P3h) -- defense in
        # depth against Spectre-style side channels and cross-origin window
        # references; COEP is 'credentialless' rather than 'require-corp' so
        # third-party provider avatars/assets that don't send CORP headers
        # still load without breaking the app.
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns (or propagates) a unique X-Request-ID per request
    (security-audit P4g) so a single request can be traced across the
    access log line, ObservabilityStore error records, and the response
    seen by the client/reverse-proxy log -- makes correlating a user's bug
    report ("it failed at 14:32") to server-side logs actually possible.
    Honors an inbound X-Request-ID from a trusted reverse proxy so a
    request ID assigned at the edge survives through to this layer."""
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


class ErrorBoundaryMiddleware(BaseHTTPMiddleware):
    """Last-resort catch-all (security-audit P4f): converts any exception
    that escapes every router/dependency/other middleware into a uniform
    JSON error response instead of an unhandled-exception traceback (which,
    depending on ASGI server config, can otherwise leak as a bare 500 with
    no body, or in the worst case a stack trace to the client). Placed
    outermost (added last / evaluated first, since Starlette middleware
    wraps in reverse registration order) so it also catches exceptions
    raised by the other middleware below it."""
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            req_id = getattr(request.state, "request_id", "unknown")
            logger.exception("[error-boundary] unhandled exception req_id=%s %s %s",
                              req_id, request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": req_id},
                headers={"X-Request-ID": req_id},
            )


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        try:
            response = await call_next(request)
            if response.status_code >= 400:
                from governance.observability import ObservabilityStore
                ObservabilityStore().record_error(
                    component="http",
                    message=f"{request.method} {request.url.path} -> {response.status_code}",
                    status_code=response.status_code,
                )
            return response
        except Exception as exc:
            from governance.observability import ObservabilityStore
            ObservabilityStore().record_error(
                component="http",
                message=f"{request.method} {request.url.path} -> 500: {exc}",
                status_code=500,
            )
            logger.exception("Unhandled request error for %s %s", request.method, request.url.path)
            raise
        finally:
            if request.url.path.startswith("/api"):
                logger.info("request %s %s finished in %.3fs", request.method, request.url.path, time.monotonic() - start)

class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF protection for cookie-authenticated state-changing requests
    (security-audit P3a).

    Bearer-token authenticated requests (Authorization header) are
    inherently immune to CSRF -- a malicious third-party site has no way to
    read localStorage or attach an Authorization header to a request it
    triggers; a script that COULD do that would already require XSS on
    this origin, a different vulnerability handled separately (CSP, input
    sanitization). The one auth path that IS exposed to CSRF is the
    `devos_token` httponly cookie, since browsers attach cookies
    automatically to same-origin-looking requests no matter who initiated
    them.

    Defense: for state-changing methods (POST/PUT/PATCH/DELETE) carrying
    the devos_token cookie with no Authorization header, require the
    Origin header (falling back to Referer) to match one of
    settings.ALLOWED_ORIGINS. This is on top of -- not instead of -- the
    cookie's SameSite=Lax attribute (see api/routes/auth.py's
    response.set_cookie call), which already blocks modern browsers from
    attaching the cookie to cross-site fetch/XHR requests; this middleware
    is defense-in-depth for older/non-compliant clients and is the part of
    the mitigation that's actually visible/testable in code."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return await call_next(request)
        # Webhook triggers (api/routes/scripts.py's POST /webhook/{token})
        # authenticate via a secret token embedded in the URL path, not
        # cookies -- there's no ambient credential for a forged cross-site
        # request to ride along on, so this route isn't CSRF-able and is
        # exempt.
        if request.url.path.startswith("/api/scripts/webhook/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        has_cookie = bool(request.cookies.get("devos_token"))
        if has_cookie and not auth_header.startswith("Bearer "):
            origin = request.headers.get("Origin") or request.headers.get("Referer", "")
            if origin:
                origin_root = origin.split("://", 1)[-1].split("/", 1)[0]
                allowed_roots = {o.split("://", 1)[-1].split("/", 1)[0] for o in settings.ALLOWED_ORIGINS}
                if origin_root not in allowed_roots:
                    req_id = getattr(request.state, "request_id", "unknown")
                    logger.warning("[csrf] blocked cross-origin state-changing request req_id=%s "
                                    "origin=%s path=%s", req_id, origin_root, request.url.path)
                    return JSONResponse(status_code=403,
                                         content={"detail": "CSRF check failed: request origin not allowed"})
            # No Origin/Referer header at all: could be a non-browser client
            # (curl, server-to-server integration) reusing the cookie
            # directly. Browsers always send Origin on cross-site
            # fetch/XHR and on same-origin navigations that matter here, so
            # a missing header isn't itself a CSRF signal in practice --
            # blocking it would break legitimate API tooling with no
            # corresponding security benefit.
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/static") or request.url.path == "/api/health":
            return await call_next(request)
        user_id = "anonymous"
        token = request.cookies.get("devos_token") or request.headers.get("Authorization","").replace("Bearer ","")
        if token:
            # Delegates to auth.py's verify_any_token() (security-audit P2/P6b)
            # instead of inlining a HS256-only jwt.decode() here -- the old
            # inline decode only understood locally-issued tokens, so any
            # Supabase-authenticated request would silently fall through to
            # user_id="anonymous" and get bucketed into a single shared rate
            # limit with every other unauthenticated caller. Import is local
            # to avoid a circular import at module load time (auth.py doesn't
            # import app.py, but api/routes/__init__ wiring makes a top-level
            # import here fragile).
            from api.routes.auth import verify_any_token
            payload, _source = verify_any_token(token)
            if payload:
                user_id = payload.get("sub", "anonymous")
        from governance.ratelimit import RateLimiter
        allowed, remaining, reason = await RateLimiter().check_api(user_id)
        if not allowed:
            return JSONResponse(status_code=429, content={"detail": reason}, headers={"Retry-After":"60"})
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

# API docs (OpenAPI/Swagger/Redoc) — disable in production unless explicitly enabled
# Production default: docs disabled (security-audit P3c)
docs_url = None if not settings.ENABLE_API_DOCS else "/docs"
redoc_url = None if not settings.ENABLE_API_DOCS else "/redoc"
openapi_url = None if not settings.ENABLE_API_DOCS else "/openapi.json"

app = FastAPI(
    title="DevOS",
    version="3.0.0",
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
)
# Middleware registration order matters (security-audit P4f/P4g): Starlette
# wraps middleware in REVERSE registration order, so the FIRST middleware
# added here becomes the OUTERMOST layer (runs first on the way in, last on
# the way out). ErrorBoundaryMiddleware is added first so it's outermost and
# can catch exceptions raised by every other middleware below it (CORS,
# security headers, observability, rate limiting) as well as route handlers.
# RequestIDMiddleware is added second (just inside the error boundary) so
# request.state.request_id is populated before anything else runs -- both
# for ErrorBoundaryMiddleware's own error responses and for every other
# middleware/handler that wants to log/correlate by request id.
app.add_middleware(ErrorBoundaryMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# CSRF (security-audit P3a): must run before CORS/route handlers so a
# forged cross-site request never reaches a state-changing endpoint just
# because it happened to carry a valid devos_token cookie.
app.add_middleware(CSRFMiddleware)
# CORS (security-audit P4a/P6h): methods/headers restricted to what the
# frontend actually sends instead of wildcards -- with allow_credentials=True,
# a wildcard allow_methods/allow_headers combined with a misconfigured
# ALLOWED_ORIGINS is a much larger blast radius than either mistake alone.
# ALLOWED_ORIGINS itself still defaults to localhost-only (see core/config.py)
# and MUST be set explicitly to the real deployed origin(s) in production.
app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS,
                   allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Request-ID"],
                   expose_headers=["X-Request-ID", "X-RateLimit-Remaining"])
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
from api.routes import auth, chat, loop, scripts, memory, search, models, health, governance, extras, files, vcs, terminal, comms, workers, secrets as secrets_routes, user_settings, nodes
from api.routes import capabilities, evidence, research, ponytail, workflow, enterprise, mcp as mcp_routes, marketplace, composer
app.include_router(auth.router,       prefix="/api/auth",       tags=["auth"])
app.include_router(chat.router,       prefix="/api/chat",       tags=["chat"])
app.include_router(loop.router,       prefix="/api/loop",       tags=["loop"])
app.include_router(scripts.router,    prefix="/api/scripts",    tags=["scripts"])
app.include_router(memory.router,     prefix="/api/memory",     tags=["memory"])
app.include_router(search.router,     prefix="/api/search",     tags=["search"])
app.include_router(models.router,     prefix="/api/models",     tags=["models"])
app.include_router(health.router,     prefix="/api/health",     tags=["health"])
app.include_router(governance.router, prefix="/api/governance", tags=["governance"])
app.include_router(extras.router,     prefix="/api/extras",     tags=["extras"])
app.include_router(files.router,      prefix="/api/files",      tags=["files"])
app.include_router(vcs.router,        prefix="/api/vcs",        tags=["vcs"])
app.include_router(terminal.router,   prefix="/api/terminal",   tags=["terminal"])
app.include_router(comms.router,      prefix="/api/comms",      tags=["comms"])
app.include_router(workers.router,    prefix="/api/workers",    tags=["workers"])
app.include_router(secrets_routes.router, prefix="/api/secrets", tags=["secrets"])
app.include_router(capabilities.router, tags=["capabilities"])
app.include_router(evidence.router,     tags=["evidence"])
app.include_router(research.router,     tags=["research"])
app.include_router(ponytail.router,     tags=["ponytail"])
app.include_router(workflow.router,     tags=["workflows"])
app.include_router(enterprise.router,   tags=["enterprise"])
app.include_router(mcp_routes.router,   prefix="/api/mcp",         tags=["mcp"])
app.include_router(marketplace.router,  prefix="/api/marketplace", tags=["marketplace"])
app.include_router(composer.router,     prefix="/api/composer",    tags=["composer"])
app.include_router(user_settings.router)
app.include_router(nodes.router)

@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa(request: Request, full_path: str):
    # Skip Jinja2 templating — index.html is a static React build
    # that doesn't need template rendering, and Starlette 0.37.2
    # has a cache-key bug with newer Jinja2 versions.
    from fastapi.responses import FileResponse
    import os
    index_path = os.path.join(os.path.dirname(__file__), "frontend", "templates", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(content="<html><body><h1>DevOS</h1><p>Frontend not found.</p></body></html>")
