"""
Brain Layer — LLM reasoning, now with custom endpoint support.
Supports: Ollama, OpenRouter, DeepSeek, Gemini, OpenAI, and ANY custom
OpenAI/Ollama-compatible endpoint (HuggingFace, VPS, home server, etc).
"""

import json
import logging
from typing import Optional
import httpx

from core.config import settings

logger = logging.getLogger("devos.brain")


class ProviderExhaustedError(RuntimeError):
    """All configured providers failed. Not a successful model response.

    Callers must treat this as execution failure — never as assistant content
    that grants capabilities or completes a mission.
    """

    def __init__(
        self,
        message: str,
        *,
        last_error: str | None = None,
        providers_tried: list | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        provider: str | None = None,
        model: str | None = None,
        category: str | None = None,
    ):
        super().__init__(message)
        self.last_error = last_error
        self.providers_tried = list(providers_tried or [])
        self.http_status = http_status
        self.retryable = retryable
        self.provider = provider
        self.model = model
        self.category = category

    def to_public_dict(self) -> dict:
        return {
            "error_type": "provider_exhausted",
            "message": str(self),
            "last_error": self.last_error,
            "providers_tried": list(self.providers_tried),
            "http_status": self.http_status,
            "retryable": self.retryable,
            "provider": self.provider,
            "model": self.model,
            "category": self.category,
        }


def classify_provider_http_status(code: int) -> dict:
    """Classify upstream HTTP status for retry/fallback policy (no secrets)."""
    try:
        code = int(code)
    except Exception:
        return {"retryable": False, "category": "unknown", "http_status": None}
    if code == 429:
        return {"retryable": True, "category": "rate_limited", "http_status": 429}
    if code in (408, 500, 502, 503, 504):
        return {"retryable": True, "category": "transient", "http_status": code}
    if code in (401, 403):
        return {"retryable": False, "category": "auth", "http_status": code}
    if code in (400, 404, 422):
        return {"retryable": False, "category": "client", "http_status": code}
    if 500 <= code < 600:
        return {"retryable": True, "category": "server", "http_status": code}
    return {"retryable": False, "category": "other", "http_status": code}


def _redact_provider_error_text(text: str, *, max_len: int = 200) -> str:
    """Strip bearer tokens / key-like substrings from upstream error bodies."""
    if not text:
        return ""
    import re
    out = text
    out = re.sub(r"(?i)(bearer\s+)[a-z0-9._\-]+", r"\1***", out)
    out = re.sub(r"(?i)(api[_-]?key[\"\s:=]+)[a-z0-9._\-]{8,}", r"\1***", out)
    out = re.sub(r"sk-[a-zA-Z0-9]{10,}", "sk-***", out)
    return out[:max_len]


# Max bounded retries for pure LLM HTTP calls (no tool side effects).
_LLM_HTTP_MAX_ATTEMPTS = 2


# Map provider id -> settings attribute for API key / host
_PROVIDER_KEY_ATTR = {
    "omniroute": "OMNIROUTE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "nararouter": "NARAROUTER_API_KEY",
}


async def load_user_provider_key(db, user_id: str, provider: str) -> Optional[str]:
    """Load PROVIDER_<ID>_KEY from the encrypted Secret store for this user."""
    if not db or not user_id or not provider:
        return None
    try:
        from sqlalchemy import select
        from core.database import Secret
        from governance.secrets_vault import decrypt
        secret_name = f"PROVIDER_{provider.upper()}_KEY"
        r = await db.execute(
            select(Secret).where(Secret.owner_id == user_id, Secret.name == secret_name)
        )
        row = r.scalar_one_or_none()
        if not row:
            return None
        return decrypt(row.encrypted_value)
    except Exception as e:
        logger.warning(f"[llm] could not load user key for {provider}: {e}")
        return None


def system_provider_key(provider: str) -> str:
    attr = _PROVIDER_KEY_ATTR.get(provider)
    if not attr:
        return ""
    return (getattr(settings, attr, None) or "").strip()

def resolve_chat_provider(*candidates: Optional[str]) -> str:
    """Resolve provider for Nuha/chat: first non-empty candidate, else DEFAULT_PROVIDER.

    Never injects a hard-coded "ollama" fallback. Explicit user/session values win.
    """
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return (settings.DEFAULT_PROVIDER or "omniroute").strip() or "omniroute"




# Structured provider health statuses for diagnostics (not a new provider layer).
PROVIDER_STATUS = (
    "NOT_CONFIGURED",
    "UNREACHABLE",
    "AUTH_FAILED",
    "MODEL_UNAVAILABLE",
    "USABLE",
)


async def probe_provider(
    provider: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 15.0,
) -> dict:
    """
    Probe one provider and classify connectivity.

    Returns:
      {
        "provider": str,
        "status": NOT_CONFIGURED | UNREACHABLE | AUTH_FAILED | MODEL_UNAVAILABLE | USABLE,
        "ok": bool,
        "detail": str,
        "sample": optional short completion when USABLE,
      }
    Does not cascade to other providers. Does not log secrets.
    """
    provider = (provider or "").strip().lower()
    result = {
        "provider": provider,
        "status": "NOT_CONFIGURED",
        "ok": False,
        "detail": "",
        "sample": None,
    }
    if not provider:
        result["detail"] = "provider is required"
        return result

    keys: dict = {}
    if api_key:
        keys[provider] = api_key
    brain = BrainLLM(provider=provider, model=model, api_keys=keys or None)

    # Pre-check configuration before network I/O
    if provider == "omniroute":
        base = (settings.OMNIROUTE_BASE_URL or "").strip()
        if not base:
            result["status"] = "NOT_CONFIGURED"
            result["detail"] = "OMNIROUTE_BASE_URL is empty"
            return result
        # Optional internal key — not required for local VPS OmniRoute
    elif provider == "ollama":
        host = (settings.OLLAMA_HOST or "").strip()
        if not host:
            result["status"] = "NOT_CONFIGURED"
            result["detail"] = "OLLAMA_HOST is empty"
            return result
    elif provider in _PROVIDER_KEY_ATTR and provider != "omniroute":
        key = brain._key_for(provider)
        if not key:
            result["status"] = "NOT_CONFIGURED"
            result["detail"] = "No API key configured for this provider"
            return result
    elif provider.startswith("custom:"):
        pass  # endpoint registry validates on call
    else:
        result["status"] = "NOT_CONFIGURED"
        result["detail"] = f"Unknown provider: {provider}"
        return result

    try:
        reply = await brain._call(
            provider,
            [{"role": "user", "content": "Reply with exactly: OK"}],
        )
        sample = (reply or "").strip()[:120]
        result["status"] = "USABLE"
        result["ok"] = True
        result["detail"] = "completion succeeded"
        result["sample"] = sample
        return result
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        # Classify without leaking secrets
        if any(x in low for x in ("api key", "no api key", "not configured", "empty", "credential")):
            result["status"] = "NOT_CONFIGURED"
        elif any(x in low for x in ("401", "403", "unauthorized", "forbidden", "invalid api", "authentication")):
            result["status"] = "AUTH_FAILED"
        elif any(x in low for x in ("connect", "unreachable", "name or service", "timed out", "timeout", "530", "502", "503", "connection refused", "cannot reach")):
            result["status"] = "UNREACHABLE"
        elif any(x in low for x in ("model", "404", "not found", "does not exist")):
            result["status"] = "MODEL_UNAVAILABLE"
        else:
            # Prefer UNREACHABLE for transport-ish, else MODEL_UNAVAILABLE as safe default for failed completion
            result["status"] = "MODEL_UNAVAILABLE"
        result["detail"] = msg[:300]
        result["ok"] = False
        return result





class BrainLLM:
    """
    The Brain. provider can be:
      "omniroute" | "ollama" | "openrouter" | "deepseek" | "gemini" | "openai"
      "custom:<endpoint_id>"   — any user-added endpoint

    Session 9 found BrainLLM() construction cost ~30ms every time, from
    httpx.AsyncClient's connection-pool/SSL setup — real, measured overhead,
    not a guess. Session 9 fixed the worst of it (multiple redundant
    constructions per run() call); this session (17b) closes the rest by
    sharing one httpx.AsyncClient process-wide instead of one per BrainLLM
    instance, following the singleton pattern already used elsewhere in
    this codebase (communications/bus.py's EventBus, governance/hitl.py's
    HITLQueue, memory/working.py's WorkingMemory, memory/graph.py's
    KnowledgeGraph). httpx.AsyncClient is explicitly designed to be reused
    concurrently across many requests — sharing it isn't a workaround, it's
    the documented, intended usage pattern for a long-lived client.
    """

    _shared_http: Optional[httpx.AsyncClient] = None

    @classmethod
    def _get_http_client(cls) -> httpx.AsyncClient:
        if cls._shared_http is None or cls._shared_http.is_closed:
            cls._shared_http = httpx.AsyncClient(timeout=120.0)
        return cls._shared_http

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None,
                 user_id: Optional[str] = None, *, purpose: str = "chat",
                 api_keys: Optional[dict] = None):
        self.provider = provider or settings.DEFAULT_PROVIDER
        self.user_id = user_id
        self.purpose = purpose  # chat | coding | reasoning | fast | vision
        # Explicit model wins; otherwise fall back to this user's preference,
        # then leave None so provider-specific system defaults apply.
        self.model = model or self._user_default_model(user_id, purpose)
        # Optional per-call key overrides (user secrets). Keyed by provider id.
        self._api_keys = {k.lower(): v for k, v in (api_keys or {}).items() if v}
        self._http = self._get_http_client()
        self.last_error: Optional[str] = None

    def _key_for(self, provider: str) -> str:
        """User override key first, then system .env / settings."""
        p = (provider or "").lower()
        if p in self._api_keys and self._api_keys[p]:
            return self._api_keys[p]
        return system_provider_key(p)

    @classmethod
    async def for_user(cls, db, user_id: str, provider: Optional[str] = None,
                       model: Optional[str] = None, *, purpose: str = "chat"):
        """Construct BrainLLM with this user's encrypted provider keys loaded."""
        from core.config import settings as _s
        keys = {}
        for pid in ("omniroute", "openrouter", "deepseek", "gemini", "openai", "huggingface", "nararouter"):
            k = await load_user_provider_key(db, user_id, pid)
            if k:
                keys[pid] = k
        return cls(provider=provider, model=model, user_id=user_id, purpose=purpose, api_keys=keys)


    @staticmethod
    def _user_default_model(user_id: Optional[str], purpose: str) -> Optional[str]:
        if not user_id:
            return None
        key = {
            "chat": "default_chat",
            "coding": "default_coding",
            "reasoning": "default_reasoning",
            "fast": "default_fast",
            "vision": "default_vision",
        }.get(purpose, "default_chat")
        try:
            # Sync lookup is intentionally avoided — callers that need DB
            # should pass model explicitly. Prefs are loaded at request
            # time in routes via resolve_user_model().
            return None
        except Exception:
            return None

    async def decide(self, messages: list[dict]) -> Optional[dict]:
        providers = [self.provider] + [
            p for p in self._all_providers() if p != self.provider
        ]
        for provider in providers:
            try:
                raw = await self._call(provider, messages)
                parsed = self._parse(raw)
                if parsed:
                    logger.debug(f"Brain [{provider}] → {parsed.get('action')}")
                    return parsed
            except Exception as e:
                logger.warning(f"Brain provider {provider} failed: {e}")
                continue
        return None

    def _all_providers(self) -> list[str]:
        """Built-in providers + this user's enabled custom endpoints, if a
        user_id was supplied. Without a user_id, custom endpoints are
        correctly excluded (they're per-user, so there's nothing safe to
        fall back to) — that's a deliberate scope limit, not the previous
        bug where this was silently a no-op regardless of whether a user_id
        existed to check."""
        base = list(settings.available_providers)
        if self.user_id:
            try:
                from brain.endpoints import EndpointRegistry
                reg = EndpointRegistry()
                custom = [f"custom:{ep.id}" for ep in reg.list_for_user(self.user_id) if ep.enabled]
                base = base + custom
            except Exception as e:
                # Logged at ERROR level (not WARNING) with the exception type
                # included — a database connection failure or schema error
                # swallowing custom endpoints is a real operational problem
                # that should be visible in logs, not silently downgraded to
                # a warning line that nobody will notice.
                logger.error(f"[llm] could not load custom endpoints for fallback: {type(e).__name__}: {e}")
        return base

    async def _call(self, provider: str, messages: list[dict]) -> str:
        if provider.startswith("custom:"):
            endpoint_id = provider.split(":", 1)[1]
            return await self._custom_endpoint(endpoint_id, messages)
        elif provider == "omniroute":
            return await self._omniroute(messages)
        elif provider == "ollama":
            return await self._ollama(messages)
        elif provider == "openrouter":
            return await self._openai_compat(
                settings.OPENROUTER_BASE_URL, self._key_for("openrouter"),
                self.model or settings.OPENROUTER_DEFAULT_MODEL, messages,
                extra_headers={"HTTP-Referer": "https://devos.local", "X-Title": "DevOS"},
            )
        elif provider == "deepseek":
            return await self._openai_compat(
                settings.DEEPSEEK_BASE_URL, self._key_for("deepseek"),
                self.model or settings.DEEPSEEK_DEFAULT_MODEL, messages,
            )
        elif provider == "gemini":
            return await self._gemini(messages)
        elif provider == "huggingface":
            return await self._openai_compat(
                settings.HUGGINGFACE_BASE_URL, self._key_for("huggingface"),
                self.model or settings.HUGGINGFACE_DEFAULT_MODEL, messages,
            )
        elif provider == "nararouter":
            return await self._openai_compat(
                settings.NARAROUTER_BASE_URL, self._key_for("nararouter"),
                self.model or settings.NARAROUTER_DEFAULT_MODEL, messages,
            )
        elif provider == "openai":
            return await self._openai_compat(
                "https://api.openai.com/v1", self._key_for("openai"),
                self.model or "gpt-4o-mini", messages,
            )
        raise ValueError(f"Unknown provider: {provider}")

    async def _custom_endpoint(self, endpoint_id: str, messages: list[dict]) -> str:
        from brain.endpoints import EndpointRegistry, CustomEndpointClient
        endpoint = EndpointRegistry().get(endpoint_id)
        if not endpoint or not endpoint.enabled:
            raise ValueError(f"Custom endpoint not found or disabled: {endpoint_id}")
        client = CustomEndpointClient(endpoint)
        try:
            return await client.chat(messages, model=self.model or endpoint.default_model)
        finally:
            await client.close()

    async def _ollama(self, messages: list[dict]) -> str:
        model = self.model or settings.OLLAMA_DEFAULT_MODEL
        host = (settings.OLLAMA_HOST or "").rstrip("/")
        if not host:
            raise ValueError("OLLAMA_HOST is empty — set it in Settings → Providers")
        try:
            r = await self._http.post(
                f"{host}/api/chat",
                json={"model": model, "messages": messages, "stream": False,
                      "options": {"temperature": 0.1}},
            )
            r.raise_for_status()
            return r.json()["message"]["content"]
        except httpx.ConnectError as e:
            raise ValueError(
                f"Cannot reach Ollama at {host} ({e}). "
                "If Ollama runs on the same machine as DevOS, try http://127.0.0.1:11434 "
                "or the host's LAN IP. From Docker use host.docker.internal:11434."
            ) from e
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:200]
            raise ValueError(f"Ollama at {host} returned {e.response.status_code}: {body}") from e


    async def _omniroute(self, messages: list[dict]) -> str:
        """Call OmniRoute OpenAI-compatible chat completions gateway.

        Upstream provider credentials stay inside OmniRoute. DevOS only needs
        OMNIROUTE_BASE_URL and optionally OMNIROUTE_API_KEY for internal auth.
        """
        base = (settings.OMNIROUTE_BASE_URL or "").rstrip("/")
        if not base:
            raise RuntimeError("OmniRoute is not configured (OMNIROUTE_BASE_URL is empty)")
        model = (self.model or settings.OMNIROUTE_DEFAULT_MODEL or "").strip()
        if not model:
            # Prefer first discovered model if no default configured
            try:
                models = await self.list_models("omniroute")
                if models:
                    model = models[0]["id"]
            except Exception:
                pass
        if not model:
            raise RuntimeError(
                "No OmniRoute model selected. Set OMNIROUTE_DEFAULT_MODEL or choose a model from the catalog."
            )
        key = self._key_for("omniroute") or (settings.OMNIROUTE_API_KEY or "").strip() or "devos"
        timeout = float(getattr(settings, "OMNIROUTE_TIMEOUT", 90.0) or 90.0)
        try:
            return await self._openai_compat(
                base, key, model, messages,
                timeout=timeout,
                require_api_key=False,
            )
        except httpx.ConnectError as e:
            raise RuntimeError(f"OmniRoute unavailable at {base}: connection failed") from e
        except httpx.TimeoutException as e:
            raise RuntimeError(f"OmniRoute timeout after {timeout}s") from e
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else "?"
            body = ""
            try:
                body = _redact_provider_error_text(
                    getattr(e, "_devos_redacted_body", None)
                    or (e.response.text if e.response is not None else "")
                    or ""
                )
            except Exception:
                pass
            if code in (400, 404):
                raise RuntimeError(f"OmniRoute invalid model or request ({code}): {body}") from e
            if code in (401, 403):
                raise RuntimeError(f"OmniRoute authentication failed ({code})") from e
            raise RuntimeError(f"OmniRoute provider failure ({code}): {body}") from e

    async def _openai_compat(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict],
        extra_headers: Optional[dict] = None,
        *,
        timeout: Optional[float] = None,
        require_api_key: bool = True,
    ) -> str:
        if require_api_key and not api_key:
            raise ValueError("No API key configured for this provider (save a user credential or system key in Settings)")
        model = self.model or model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        last_exc: Exception | None = None
        attempts = max(1, int(_LLM_HTTP_MAX_ATTEMPTS))
        for attempt in range(1, attempts + 1):
            try:
                r = await self._http.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    json={"model": model, "messages": messages, "temperature": 0.1},
                    headers=headers,
                    timeout=timeout,
                )
                r.raise_for_status()
                try:
                    data = r.json()
                except Exception as e:
                    raise RuntimeError(f"Malformed provider response (non-JSON): {e}") from e
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as e:
                    raise RuntimeError(f"Malformed provider response (missing choices): {e}") from e
                if content is None or (isinstance(content, str) and not content.strip()):
                    raise RuntimeError("Empty provider response (no message content)")
                return content if isinstance(content, str) else str(content)
            except httpx.HTTPStatusError as e:
                # Do not retry auth/client errors
                code = e.response.status_code if e.response is not None else 0
                body = ""
                try:
                    body = _redact_provider_error_text(e.response.text or "")
                except Exception:
                    pass
                if code in (401, 403, 400, 404, 422):
                    e._devos_redacted_body = body  # type: ignore[attr-defined]
                    raise
                last_exc = e
                if attempt >= attempts:
                    e._devos_redacted_body = body  # type: ignore[attr-defined]
                    raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt >= attempts:
                    raise
                logger.warning("LLM HTTP attempt %s/%s failed (%s); retrying", attempt, attempts, type(e).__name__)
            except RuntimeError:
                raise
            except Exception as e:
                last_exc = e
                if attempt >= attempts:
                    raise
        if last_exc:
            raise last_exc
        raise RuntimeError("LLM HTTP call failed with no response")

    async def _gemini(self, messages: list[dict]) -> str:
        if not self._key_for("gemini"):
            raise ValueError("No Gemini API key")
        model = self.model or settings.GEMINI_DEFAULT_MODEL
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={self._key_for('gemini')}")
        contents = []
        system_text = ""
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
        payload = {"contents": contents}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        r = await self._http.post(url, json=payload)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    def _parse(self, raw: str) -> Optional[dict]:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        for fence in ["```json", "```"]:
            if fence in raw:
                try:
                    start = raw.index(fence) + len(fence)
                    end = raw.index("```", start)
                    return json.loads(raw[start:end].strip())
                except (ValueError, json.JSONDecodeError):
                    pass
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            pass
        logger.warning("Brain response not parseable (refusing mark_complete): %s", raw[:200])
        # Never grant completion/capability from an unparseable model blob.
        return None

    async def stream_chat(self, messages: list[dict], *, allow_fallback: bool = True) -> str:
        """Call the configured provider (and optionally fall back).

        On total failure raises ProviderExhaustedError — never returns a
        synthetic "All providers failed" assistant message that callers might
        treat as successful model content.
        """
        primary = self.provider
        providers = [primary]
        if allow_fallback:
            providers = [primary] + [p for p in self._all_providers() if p != primary]
        last_error = None
        tried: list[str] = []
        for provider in providers:
            tried.append(provider)
            try:
                return await self._call(provider, messages)
            except Exception as e:
                # Never log raw secrets; exception messages are already redacted upstream where possible
                last_error = f"{provider}: {type(e).__name__}: {e}"
                self.last_error = last_error
                logger.warning("Chat provider %s failed: %s", provider, type(e).__name__)
        detail = last_error or "no providers attempted"
        http_status = None
        retryable = None
        category = None
        if last_error and "429" in str(last_error):
            http_status = 429
            retryable = True
            category = "rate_limited"
        raise ProviderExhaustedError(
            f"All providers failed. Last error: {detail}",
            last_error=detail,
            providers_tried=tried,
            http_status=http_status,
            retryable=retryable,
            provider=tried[-1] if tried else None,
            model=getattr(self, "model", None),
            category=category,
        )

    async def list_models(self, provider: Optional[str] = None) -> list[dict]:
        provider = provider or self.provider

        if provider.startswith("custom:"):
            from brain.endpoints import EndpointRegistry, CustomEndpointClient
            endpoint = EndpointRegistry().get(provider.split(":", 1)[1])
            if not endpoint:
                return []
            client = CustomEndpointClient(endpoint)
            try:
                models = await client.list_models()
                return [{**m, "provider": provider} for m in models]
            finally:
                await client.close()

        if provider == "omniroute":
            base = (settings.OMNIROUTE_BASE_URL or "").rstrip("/")
            if not base:
                return []
            try:
                headers = {}
                key = (settings.OMNIROUTE_API_KEY or "").strip()
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                r = await self._http.get(f"{base}/models", headers=headers or None)
                r.raise_for_status()
                data = r.json()
                items = data.get("data") if isinstance(data, dict) else data
                out = []
                for m in items or []:
                    if isinstance(m, str):
                        mid = m
                        name = m
                        free = ":free" in m.lower() or "free" in m.lower()
                    else:
                        mid = m.get("id") or m.get("name") or ""
                        name = m.get("name") or mid
                        free = bool(m.get("free")) or ":free" in str(mid).lower()
                    if mid:
                        out.append({
                            "id": mid,
                            "name": name,
                            "provider": "omniroute",
                            "free": free,
                        })
                return out
            except Exception as e:
                logger.warning("OmniRoute model list failed: %s", e)
                return []
        if provider == "ollama":
            try:
                r = await self._http.get(f"{settings.OLLAMA_HOST.rstrip('/')}/api/tags")
                r.raise_for_status()
                return [{"id": m["name"], "name": m["name"], "provider": "ollama",
                          "size": m.get("size", 0)} for m in r.json().get("models", [])]
            except Exception as e:
                logger.warning(f"Ollama model list failed: {e}")
                return []
        elif provider == "openrouter":
            try:
                r = await self._http.get(f"{settings.OPENROUTER_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"})
                r.raise_for_status()
                return [{"id": m["id"], "name": m.get("name", m["id"]),
                          "provider": "openrouter", "free": ":free" in m["id"]}
                        for m in r.json().get("data", [])]
            except Exception:
                return []
        elif provider == "deepseek":
            return [{"id": "deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek"},
                    {"id": "deepseek-coder", "name": "DeepSeek Coder", "provider": "deepseek"}]
        elif provider == "gemini":
            return [{"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash (Free)", "provider": "gemini"},
                    {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "provider": "gemini"}]
        elif provider == "huggingface":
            # HF has thousands of models with no simple "list what's free"
            # API the way OpenRouter has — this is a curated set of
            # well-supported chat models on HF's Inference Providers router,
            # not a live catalog query.
            return [
                {"id": "meta-llama/Llama-3.3-70B-Instruct:auto", "name": "Llama 3.3 70B (auto-routed)", "provider": "huggingface"},
                {"id": "deepseek-ai/DeepSeek-R1:auto", "name": "DeepSeek R1 (auto-routed)", "provider": "huggingface"},
                {"id": "Qwen/Qwen2.5-Coder-32B-Instruct:auto", "name": "Qwen 2.5 Coder 32B (auto-routed)", "provider": "huggingface"},
            ]
        elif provider == "nararouter":
            return [
                {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat (Nararouter)", "provider": "nararouter"},
                {"id": "deepseek/deepseek-coder", "name": "DeepSeek Coder (Nararouter)", "provider": "nararouter"},
                {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini (Nararouter)", "provider": "nararouter"},
                {"id": "openai/gpt-4o", "name": "GPT-4o (Nararouter)", "provider": "nararouter"},
                {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet (Nararouter)", "provider": "nararouter"},
                {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B (Nararouter)", "provider": "nararouter"},
            ]
        return []

    async def close(self):
        """No-op now that the HTTP client is shared process-wide (see
        _get_http_client above) — closing it here would break every other
        BrainLLM instance currently running, not just this one. Nothing in
        this codebase currently calls this method (checked before making
        this change); kept as a real, harmless no-op rather than removed
        outright, in case something starts calling it expecting per-instance
        cleanup semantics — that expectation would now be wrong, and this
        docstring is where a future reader finds out why."""
        pass


async def resolve_user_model(db, user_id: str, purpose: str = "chat",
                             explicit: Optional[str] = None) -> Optional[str]:
    """Agent/request explicit model → user preference → None (system default).

    Tenant defaults are not implemented; document Agent → User → System.
    """
    if explicit:
        return explicit
    if not user_id:
        return None
    try:
        from sqlalchemy import select
        from core.database import UserSettings
        r = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        row = r.scalar_one_or_none()
        if not row or not row.settings_json:
            return None
        models = (row.settings_json.get("models") or {})
        key = {
            "chat": "default_chat",
            "coding": "default_coding",
            "reasoning": "default_reasoning",
            "fast": "default_fast",
            "vision": "default_vision",
        }.get(purpose, "default_chat")
        val = (models.get(key) or "").strip()
        return val or None
    except Exception:
        return None
