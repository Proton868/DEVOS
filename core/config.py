"""DevOS Core Config"""
import os
import secrets
from typing import List
from pydantic_settings import BaseSettings

# Persisted secret file for JWT_SECRET — if the user doesn't set JWT_SECRET
# in .env, we generate one once and write it here so restarts don't silently
# invalidate all sessions and encrypted secrets (see record.md Session 6 and
# the bug audit at plans/bug-audit-plan.md). Path is relative to the config
# module's directory, so it ends up in core/ alongside this file.
_SECRET_PERSIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     ".devos_secret")

def _get_or_create_persisted_secret() -> str:
    """Read JWT_SECRET from the persisted file if it exists; otherwise
    generate a new one, write it, and return it. This ensures the secret
    survives process restarts when the user hasn't set it explicitly in .env.
    The file is created with owner-only permissions (0o600)."""
    if os.path.exists(_SECRET_PERSIST_PATH):
        with open(_SECRET_PERSIST_PATH, "r") as f:
            return f.read().strip()
    import secrets
    secret = secrets.token_hex(32)
    with open(_SECRET_PERSIST_PATH, "w") as f:
        os.chmod(_SECRET_PERSIST_PATH, 0o600)
        f.write(secret)
    return secret


class Settings(BaseSettings):
    APP_NAME: str = "DevOS"
    SECRET_KEY: str = ""  # unused; kept for backward-compat
    DEBUG: bool = False
    ALLOWED_ORIGINS: List[str] = ["http://localhost:8000"]

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/devos.db"

    AUTH_ENABLED: bool = True
    JWT_SECRET: str = ""  # default empty — replace with persisted secret if not set in .env

    # security-audit P2b: which identity provider(s) get_current_user() will
    # accept, on top of the existing "local token first, then Supabase"
    # decode order (see api/routes/auth.py's docstring for the full dual-auth
    # design). This does NOT change that order — it only lets an operator
    # narrow it down for deployments that want a single, predictable auth
    # source instead of accepting either:
    #   "dual"     (default) — accept both local JWTs and Supabase tokens,
    #               exactly like the original P2 behavior. Recommended for
    #               production per this task's explicit architecture choice
    #               ("Supabase-primary with local fallback").
    #   "local"    — ignore Supabase tokens entirely, even if SUPABASE_URL/
    #               SUPABASE_KEY are set (e.g. those creds are only used for
    #               something else, like storage). Local username/password
    #               login is the only accepted path.
    #   "supabase" — ignore locally-issued JWTs (decode_local_token is never
    #               tried), so every request must carry a Supabase access
    #               token. NOTE: the bootstrap admin account created by
    #               _create_admin() still has a local password and can still
    #               call POST /api/auth/login to obtain a local JWT — but
    #               that JWT will be rejected by get_current_user() in this
    #               mode. Only switch to this mode after confirming your
    #               admin identity has been synced into Supabase.
    AUTH_MODE: str = "dual"  # "dual" | "local" | "supabase"

    # security-audit P4b: structured logging. "text" keeps the traditional
    # human-readable console format (best for local dev, `docker logs`, etc);
    # "json" emits one JSON object per line (timestamp, level, logger name,
    # message, plus request_id/exception info when present) so a production
    # log aggregator (ELK, Loki, CloudWatch, Datadog, etc.) can index/query
    # fields directly instead of regex-parsing free text.
    LOG_FORMAT: str = "text"  # "text" | "json"

    def model_post_init(self, __context):
        """If JWT_SECRET wasn't set in .env, fall back to a persisted secret
        that survives process restarts. This closes the bug where every restart
        silently generated a new random secret, invalidating all sessions and
        making all encrypted secrets undecryptable."""
        if not self.JWT_SECRET:
            self.JWT_SECRET = _get_or_create_persisted_secret()
    JWT_EXPIRE_HOURS: int = 168
    ADMIN_USER: str = "admin"
    ADMIN_EMAIL: str = "admin@localhost"
    ADMIN_PASSWORD: str = ""

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    # Optional: only needed to verify legacy HS256 Supabase access tokens
    # (pre-2024 Supabase projects). Found in the Supabase Dashboard under
    # Settings -> API -> JWT Secret. Newer Supabase projects sign tokens
    # with RS256/ES256 and are verified against Supabase's published JWKS
    # instead (no secret needed) — see api/routes/auth.py's
    # _decode_supabase_jwt(). Deliberately NOT in EDITABLE_PROVIDER_KEYS
    # below (security-sensitive, same treatment as JWT_SECRET).
    SUPABASE_JWT_SECRET: str = ""

    # LLMs
    OLLAMA_HOST: str = "https://ollama.carai.agency"
    OLLAMA_DEFAULT_MODEL: str = "llama3"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_DEFAULT_MODEL: str = "mistralai/mistral-7b-instruct:free"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_DEFAULT_MODEL: str = "deepseek-chat"
    GEMINI_API_KEY: str = ""
    GEMINI_DEFAULT_MODEL: str = "gemini-1.5-flash"
    OPENAI_API_KEY: str = ""
    # HuggingFace's OpenAI-compatible router — a real HF token is required
    # even for "free" models (rate-limited monthly free credits, not
    # anonymous access), confirmed against HF's current docs as of this
    # audit. Model IDs need a provider suffix (e.g. "meta-llama/Llama-3.3-70B-Instruct:auto")
    # or they default to HF's automatic "fastest" routing.
    HUGGINGFACE_API_KEY: str = ""
    HUGGINGFACE_BASE_URL: str = "https://router.huggingface.co/v1"
    HUGGINGFACE_DEFAULT_MODEL: str = "meta-llama/Llama-3.3-70B-Instruct:auto"
    NARAROUTER_API_KEY: str = ""
    NARAROUTER_BASE_URL: str = "https://router.bynara.id/v1"
    NARAROUTER_DEFAULT_MODEL: str = "deepseek/deepseek-chat"
    DEFAULT_PROVIDER: str = "ollama"

    # Search
    TAVILY_API_KEY: str = ""
    SEARXNG_URL: str = "http://localhost:8080"

    # Memory
    CHROMADB_HOST: str = "localhost"
    CHROMADB_PORT: int = 8100
    ENCRYPTION_KEY: str = ""

    # Script runner
    SCRIPT_TIMEOUT: int = 60
    WEBHOOK_SECRET: str = secrets.token_hex(16)

    # Worker concurrency (for uvicorn --workers)
    # micro: 1 (personal use), standard: 2, enterprise: 4
    WEB_CONCURRENCY: int = 1

    # API documentation (OpenAPI/Swagger/Redoc) — disable in production
    ENABLE_API_DOCS: bool = True

    # PTY terminal idle timeout (seconds) — kill abandoned shell sessions
    # after this many seconds with no attached connections and no output.
    # 0 = never timeout (disable reaper).
    PTY_IDLE_TIMEOUT: int = 1800  # 30 minutes

    # Notifications
    NTFY_URL: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Integrations
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    IMAP_USER: str = ""
    IMAP_PASSWORD: str = ""
    CALDAV_URL: str = ""
    CALDAV_USER: str = ""
    CALDAV_PASSWORD: str = ""

    @property
    def has_supabase(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_KEY)

    @property
    def has_tavily(self) -> bool:
        return bool(self.TAVILY_API_KEY)

    @property
    def available_providers(self) -> List[str]:
        p = ["ollama"]
        if self.OPENROUTER_API_KEY:   p.append("openrouter")
        if self.DEEPSEEK_API_KEY:     p.append("deepseek")
        if self.GEMINI_API_KEY:       p.append("gemini")
        if self.OPENAI_API_KEY:       p.append("openai")
        if self.HUGGINGFACE_API_KEY:  p.append("huggingface")
        if self.NARAROUTER_API_KEY:   p.append("nararouter")
        return p

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# ── Runtime-editable provider settings ─────────────────────────────────
# Keys a user is allowed to change from the Settings UI (Provider cards) at
# runtime, persisted to .env so they survive restarts. Deliberately excludes
# anything security-sensitive (JWT_SECRET, ADMIN_PASSWORD, WEBHOOK_SECRET,
# ENCRYPTION_KEY) — those must be changed by editing .env directly.
EDITABLE_PROVIDER_KEYS = [
    "DEFAULT_PROVIDER",
    "OLLAMA_HOST", "OLLAMA_DEFAULT_MODEL",
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_DEFAULT_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_DEFAULT_MODEL",
    "GEMINI_API_KEY", "GEMINI_DEFAULT_MODEL",
    "OPENAI_API_KEY",
    "HUGGINGFACE_API_KEY", "HUGGINGFACE_BASE_URL", "HUGGINGFACE_DEFAULT_MODEL",
    "NARAROUTER_API_KEY", "NARAROUTER_BASE_URL", "NARAROUTER_DEFAULT_MODEL",
    "SUPABASE_URL", "SUPABASE_KEY",
    "TAVILY_API_KEY",
]

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def update_env_settings(updates: dict) -> dict:
    """Persist a whitelisted set of settings to .env AND apply them to the
    live `settings` singleton immediately, so changes made from the Settings
    UI take effect without a server restart. Returns the keys actually
    changed. Raises ValueError if an unknown/forbidden key is passed."""
    unknown = [k for k in updates if k not in EDITABLE_PROVIDER_KEYS]
    if unknown:
        raise ValueError(f"Not editable via API: {', '.join(unknown)}")

    env_path = os.path.abspath(_ENV_PATH)
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

    remaining = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}\n"

    for key, value in remaining.items():
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

    for key, value in updates.items():
        setattr(settings, key, value)

    return updates
