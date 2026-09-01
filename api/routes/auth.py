"""Auth routes

Dual-mode authentication (security-audit P2): Supabase-primary with local
fallback, per the explicit architecture decision made for this task ("proceed
with Supabase-primary with local fallback (recommended for production)").

How it works:
  - Local accounts (bootstrap admin, or any account created before Supabase
    was configured) keep working exactly as before: bcrypt password hash +
    HS256 JWT signed with settings.JWT_SECRET, issued by POST /login and
    carried in either the `devos_token` httponly cookie or an
    `Authorization: Bearer` header.
  - When settings.has_supabase is true, the frontend authenticates directly
    against Supabase (supabase.auth.signInWithPassword()) and sends
    Supabase's own access token to the backend instead. get_current_user()
    detects which kind of token it received (by trying local HS256
    verification first, then falling back to Supabase verification) and,
    for Supabase tokens, transparently syncs a local `User` row (keyed by
    the new `User.supabase_id` column) so the rest of the codebase — which
    is written entirely in terms of local `User` objects/`User.id` foreign
    keys — doesn't need to know or care which identity provider was used.
  - If Supabase's project still uses the legacy shared JWT secret (HS256,
    pre-2024 projects), set SUPABASE_JWT_SECRET and that path is used
    instead of JWKS.
"""
import functools
import logging
import secrets
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import bcrypt, jwt
from datetime import datetime, timezone, timedelta
from core.config import settings
from core.database import get_db, User

logger = logging.getLogger("devos.auth")
router = APIRouter()

# JWT issuer/audience claims (security-audit P3b) — binds tokens to this
# specific application so a JWT minted for some other service that happens
# to share a secret (or a Supabase project shared across multiple apps)
# can't be replayed here, and vice versa.
JWT_ISSUER = "devos"
JWT_AUDIENCE = "devos-api"


def hash_pw(pw): return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
def check_pw(pw, h): return bcrypt.checkpw(pw.encode(), h.encode())


def make_jwt(uid, admin=False, expire_hours: int = None):
    """Mint a local HS256 JWT. `expire_hours` overrides settings.JWT_EXPIRE_HOURS
    (used for shorter-lived tokens if a caller wants that in the future)."""
    hours = expire_hours if expire_hours is not None else settings.JWT_EXPIRE_HOURS
    return jwt.encode(
        {
            "sub": uid,
            "admin": admin,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def decode_local_token(token: str):
    """Verify a locally-issued HS256 JWT. Returns the payload dict, or None
    if the token isn't a valid local token (including: it's actually a
    Supabase token, which will fail here since it's signed with a different
    key/algorithm and won't have our iss/aud claims — that's the intended
    signal to fall through to decode_supabase_token())."""
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=["HS256"],
            issuer=JWT_ISSUER, audience=JWT_AUDIENCE,
        )
    except Exception:
        return None


@functools.lru_cache(maxsize=4)
def _jwks_client_for(jwks_url: str):
    """Cached per JWKS URL so we don't rebuild (and re-fetch) the key set on
    every request. PyJWKClient itself caches the fetched keys internally
    (cache_keys=True) for `lifespan` seconds, so this is a two-level cache:
    one client instance per URL, and that client's own internal key cache."""
    from jwt import PyJWKClient
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=300)


def decode_supabase_token(token: str):
    """Verify a Supabase-issued access token. Tries modern RS256/ES256
    verification against Supabase's published JWKS first (no shared secret
    needed — this is how current Supabase projects sign tokens), then falls
    back to legacy HS256 + SUPABASE_JWT_SECRET for older Supabase projects
    that still use a shared JWT secret. Returns the payload dict, or None if
    neither verification path succeeds (or Supabase isn't configured)."""
    if not settings.SUPABASE_URL:
        return None

    jwks_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        signing_key = _jwks_client_for(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token, signing_key.key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
        )
    except Exception as e:
        logger.debug("[auth] Supabase JWKS verification failed, trying legacy HS256: %s", e)

    if settings.SUPABASE_JWT_SECRET:
        try:
            return jwt.decode(
                token, settings.SUPABASE_JWT_SECRET, algorithms=["HS256"],
                audience="authenticated",
            )
        except Exception as e:
            logger.debug("[auth] Supabase legacy HS256 verification failed: %s", e)

    return None


def verify_any_token(token: str):
    """Lightweight (no DB access) token verification used by call sites that
    only need a stable identifier for the caller — not a full local `User`
    row — such as RateLimitMiddleware's per-user bucketing and the SSE
    stream's pub/sub topic key. Tries the local path first (cheap, no
    network I/O), then Supabase.

    Respects settings.AUTH_MODE (security-audit P2b): "local" skips the
    Supabase attempt entirely; "supabase" skips the local attempt entirely;
    "dual" (default) tries both, local first.

    Returns (payload, source) where source is "local" or "supabase", or
    (None, None) if the token doesn't verify against either.
    """
    if settings.AUTH_MODE != "supabase":
        payload = decode_local_token(token)
        if payload is not None:
            return payload, "local"
    if settings.AUTH_MODE != "local" and settings.has_supabase:
        payload = decode_supabase_token(token)
        if payload is not None:
            return payload, "supabase"
    return None, None


async def sync_supabase_user(db: AsyncSession, payload: dict) -> User:
    """Find-or-create the local `User` row backing a Supabase identity
    (security-audit P2a/P2f). Lookup order:
      1. By `supabase_id` (fast path — already synced before).
      2. By `email` (links a Supabase login to a pre-existing local account
         with the same email, e.g. an admin who later enables Supabase —
         avoids creating a duplicate/orphaned second account for the same
         person).
      3. Otherwise create a brand-new local User row with no local password
         (Supabase-only account; `hashed_password` stays NULL so the local
         /api/auth/login path can never be used for it, since check_pw()
         would fail safely against a NULL/empty hash anyway, but we make
         the intent explicit here).
    Every path updates `supabase_id` and `email` to keep them in sync with
    whatever Supabase currently reports.
    """
    supabase_id = payload.get("sub")
    email = (payload.get("email") or "").lower() or None
    if not supabase_id:
        raise HTTPException(401, "Supabase token missing 'sub' claim")

    r = await db.execute(select(User).where(User.supabase_id == supabase_id))
    user = r.scalar_one_or_none()

    if user is None and email:
        r = await db.execute(select(User).where(User.email == email))
        user = r.scalar_one_or_none()

    if user is None:
        from core.database import gen_id
        username = (email.split("@")[0] if email else f"supabase_{supabase_id[:8]}")
        user = User(
            id=gen_id(),
            username=username,
            email=email or f"{supabase_id}@supabase.local",
            hashed_password=None,
            supabase_id=supabase_id,
            is_admin=False,
            is_active=True,
        )
        db.add(user)
        logger.info("[auth] created local user for new Supabase identity sub=%s", supabase_id)
    else:
        changed = False
        if user.supabase_id != supabase_id:
            user.supabase_id = supabase_id
            changed = True
        if email and user.email != email:
            user.email = email
            changed = True
        if changed:
            logger.info("[auth] synced local user %s with Supabase identity sub=%s", user.id, supabase_id)

    await db.commit()
    await db.refresh(user)
    return user


def _extract_token(request: Request) -> str:
    token = None
    if request and hasattr(request, "cookies"):
        token = request.cookies.get("devos_token")
    if not token and request:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


async def get_current_user(request: Request = None, db: AsyncSession = Depends(get_db)):
    """Resolves the authenticated `User` for a request. Which token types are
    accepted is governed by settings.AUTH_MODE (security-audit P2b): "dual"
    (default) tries a local JWT first then falls back to Supabase, "local"
    never attempts Supabase verification even if configured, and "supabase"
    never attempts local JWT verification (see AUTH_MODE's docstring in
    core/config.py for full deployment guidance)."""
    token = _extract_token(request)
    if not token:
        if not settings.AUTH_ENABLED:
            r = await db.execute(select(User).where(User.is_admin == True).limit(1))
            user = r.scalar_one_or_none()
            if user is None:
                raise HTTPException(401, "No admin user found — run the setup script first")
            return user
        raise HTTPException(401, "Not authenticated")

    if settings.AUTH_MODE != "supabase":
        payload = decode_local_token(token)
        if payload is not None:
            r = await db.execute(select(User).where(User.id == payload["sub"]))
            user = r.scalar_one_or_none()
            if user is None:
                raise HTTPException(401, "Invalid token")
            return user

    if settings.AUTH_MODE != "local" and settings.has_supabase:
        payload = decode_supabase_token(token)
        if payload is not None:
            return await sync_supabase_user(db, payload)

    raise HTTPException(401, "Invalid token")


class LoginReq(BaseModel):
    username: str
    password: str

    @field_validator("username", "password")
    @classmethod
    def _non_empty(cls, v):
        # Defensive only — FastAPI/Pydantic already rejects missing fields;
        # this guards against whitespace-only submissions bypassing that.
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v


def _client_ip(request: Request) -> str:
    """Best-effort client IP for login rate-limiting (security-audit P4h).
    Trusts X-Forwarded-For only as a last resort since it's trivially
    spoofable by the client unless a reverse proxy is guaranteed to
    overwrite it — falls back to the direct connection's address."""
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login(req: LoginReq, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    from governance.ratelimit import RateLimiter
    from governance.audit import AuditLogger, AuditEventType

    ip = _client_ip(request)
    allowed, reason = await RateLimiter().check_login(ip)
    if not allowed:
        AuditLogger().log(AuditEventType.LOGIN_FAILURE, actor_id=req.username, tenant_id="default",
                           action="login", outcome="rate_limited", details={"ip": ip})
        raise HTTPException(429, reason)

    r = await db.execute(select(User).where(User.username == req.username))
    user = r.scalar_one_or_none()
    if not user or not user.hashed_password or not check_pw(req.password, user.hashed_password):
        AuditLogger().log(AuditEventType.LOGIN_FAILURE, actor_id=req.username, tenant_id="default",
                           action="login", outcome="invalid_credentials", details={"ip": ip})
        raise HTTPException(401, "Invalid credentials")

    token = make_jwt(user.id, user.is_admin)
    response.set_cookie("devos_token", token, httponly=True, samesite="lax",
                        secure=not settings.DEBUG,
                        max_age=settings.JWT_EXPIRE_HOURS*3600)
    AuditLogger().log(AuditEventType.LOGIN_SUCCESS, actor_id=user.id, tenant_id="default",
                       action="login", outcome="success", details={"ip": ip})
    return {"token": token, "user": {"id": user.id, "username": user.username,
                                      "email": user.email, "is_admin": user.is_admin}}


@router.post("/logout")
async def logout(response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    from governance.audit import AuditLogger, AuditEventType
    token = _extract_token(request)
    actor_id = "unknown"
    if token:
        payload = decode_local_token(token) or (decode_supabase_token(token) if settings.has_supabase else None)
        if payload:
            actor_id = payload.get("sub", "unknown")
    response.delete_cookie("devos_token")
    AuditLogger().log(AuditEventType.LOGOUT, actor_id=actor_id, tenant_id="default", action="logout", outcome="success")
    return {"status": "ok"}


class SupabaseTokenReq(BaseModel):
    token: str


@router.post("/supabase/exchange")
async def supabase_exchange(req: SupabaseTokenReq, response: Response, db: AsyncSession = Depends(get_db)):
    """Exchange a Supabase access token (from OAuth or phone OTP) for a
    local DevOS JWT. The caller must already have completed the Supabase
    sign-in flow; this endpoint only verifies the token and issues a
    local session."""
    if not settings.has_supabase:
        raise HTTPException(400, "Supabase is not configured on this server")

    payload = decode_supabase_token(req.token)
    if payload is None:
        raise HTTPException(401, "Invalid Supabase token")

    from governance.audit import AuditLogger, AuditEventType
    user = await sync_supabase_user(db, payload)
    local_token = make_jwt(user.id, user.is_admin)
    response.set_cookie("devos_token", local_token, httponly=True, samesite="lax",
                        secure=not settings.DEBUG,
                        max_age=settings.JWT_EXPIRE_HOURS*3600)
    AuditLogger().log(AuditEventType.LOGIN_SUCCESS, actor_id=user.id, tenant_id="default",
                       action="supabase_exchange", outcome="success")
    return {"token": local_token, "user": {"id": user.id, "username": user.username,
                                            "email": user.email, "is_admin": user.is_admin,
                                            "supabase_linked": True}}


@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user: raise HTTPException(401)
    return {"id": user.id, "username": user.username, "email": user.email,
            "is_admin": user.is_admin, "supabase_linked": bool(user.supabase_id)}


class SupabaseTokenReq(BaseModel):
    token: str


@router.post("/supabase/exchange")
async def supabase_exchange(req: SupabaseTokenReq, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    """Exchange a Supabase access token (from OAuth or phone OTP) for a
    local DevOS JWT. The caller must already have completed the Supabase
    sign-in flow; this endpoint only verifies the token and issues a
    local session."""
    if not settings.has_supabase:
        raise HTTPException(400, "Supabase is not configured on this server")

    payload = decode_supabase_token(req.token)
    if payload is None:
        raise HTTPException(401, "Invalid Supabase token")

    from governance.audit import AuditLogger, AuditEventType
    user = await sync_supabase_user(db, payload)
    local_token = make_jwt(user.id, user.is_admin)
    response.set_cookie("devos_token", local_token, httponly=True, samesite="lax",
                        secure=not settings.DEBUG,
                        max_age=settings.JWT_EXPIRE_HOURS*3600)
    AuditLogger().log(AuditEventType.LOGIN_SUCCESS, actor_id=user.id, tenant_id="default",
                       action="supabase_exchange", outcome="success")
    return {"token": local_token, "user": {"id": user.id, "username": user.username,
                                            "email": user.email, "is_admin": user.is_admin,
                                            "supabase_linked": True}}


@router.post("/supabase/sync")
async def supabase_sync(request: Request, db: AsyncSession = Depends(get_db)):
    """Called by the frontend right after a successful
    supabase.auth.signInWithPassword() (or on session restore), so the
    backend gets a chance to create/update the local User row before the
    user hits any other endpoint (security-audit P2f). Idempotent — safe to
    call on every app load."""
    if not settings.has_supabase:
        raise HTTPException(400, "Supabase is not configured on this server")

    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "Missing Supabase access token")

    payload = decode_supabase_token(token)
    if payload is None:
        raise HTTPException(401, "Invalid Supabase token")

    from governance.audit import AuditLogger, AuditEventType
    user = await sync_supabase_user(db, payload)
    AuditLogger().log(AuditEventType.LOGIN_SUCCESS, actor_id=user.id, tenant_id="default",
                       action="supabase_sync", outcome="success")
    return {"id": user.id, "username": user.username, "email": user.email,
            "is_admin": user.is_admin, "supabase_linked": True}
