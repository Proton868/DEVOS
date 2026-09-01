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


class BrainLLM:
    """
    The Brain. provider can be:
      "ollama" | "openrouter" | "deepseek" | "gemini" | "openai"
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
                 user_id: Optional[str] = None):
        self.provider = provider or settings.DEFAULT_PROVIDER
        self.model = model
        self.user_id = user_id
        self._http = self._get_http_client()

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
        elif provider == "ollama":
            return await self._ollama(messages)
        elif provider == "openrouter":
            return await self._openai_compat(
                settings.OPENROUTER_BASE_URL, settings.OPENROUTER_API_KEY,
                settings.OPENROUTER_DEFAULT_MODEL, messages,
                extra_headers={"HTTP-Referer": "https://devos.local", "X-Title": "DevOS"},
            )
        elif provider == "deepseek":
            return await self._openai_compat(
                settings.DEEPSEEK_BASE_URL, settings.DEEPSEEK_API_KEY,
                settings.DEEPSEEK_DEFAULT_MODEL, messages,
            )
        elif provider == "gemini":
            return await self._gemini(messages)
        elif provider == "huggingface":
            return await self._openai_compat(
                settings.HUGGINGFACE_BASE_URL, settings.HUGGINGFACE_API_KEY,
                settings.HUGGINGFACE_DEFAULT_MODEL, messages,
            )
        elif provider == "nararouter":
            return await self._openai_compat(
                settings.NARAROUTER_BASE_URL, settings.NARAROUTER_API_KEY,
                settings.NARAROUTER_DEFAULT_MODEL, messages,
            )
        elif provider == "openai":
            return await self._openai_compat(
                "https://api.openai.com/v1", settings.OPENAI_API_KEY,
                "gpt-4o-mini", messages,
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
        r = await self._http.post(
            f"{settings.OLLAMA_HOST.rstrip('/')}/api/chat",
            json={"model": model, "messages": messages, "stream": False,
                  "options": {"temperature": 0.1}},
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def _openai_compat(self, base_url: str, api_key: str, model: str,
                               messages: list[dict],
                               extra_headers: Optional[dict] = None) -> str:
        if not api_key:
            raise ValueError("No API key")
        model = self.model or model
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        r = await self._http.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={"model": model, "messages": messages, "temperature": 0.1},
            headers=headers,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def _gemini(self, messages: list[dict]) -> str:
        if not settings.GEMINI_API_KEY:
            raise ValueError("No Gemini API key")
        model = self.model or settings.GEMINI_DEFAULT_MODEL
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={settings.GEMINI_API_KEY}")
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
        logger.warning(f"Brain response not parseable, treating as answer: {raw[:200]}")
        return {"thought": "Could not parse structured response",
                "action": "mark_complete", "action_input": raw,
                "description": "Unstructured response treated as final answer"}

    async def stream_chat(self, messages: list[dict]) -> str:
        providers = [self.provider] + [
            p for p in self._all_providers() if p != self.provider
        ]
        for provider in providers:
            try:
                return await self._call(provider, messages)
            except Exception as e:
                logger.warning(f"Chat provider {provider} failed: {e}")
        return "All providers failed. Check your API keys, endpoints, and Ollama connection."

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
