"""
Execution Layer — Web Search.
The Brain calls search_web() to get information.
Like code execution, this is a capability the Brain USES, not drives itself.
"""

import httpx
from core.config import settings

# Shared HTTP client — same pattern as brain/llm.py's _get_http_client()
# (Session 18 + record.md). httpx.AsyncClient is explicitly designed to be
# reused across many concurrent requests; constructing a new one per call
# was paying ~30ms of connection-pool/SSL setup overhead every time.
_shared_http: httpx.AsyncClient = None


def _get_http_client() -> httpx.AsyncClient:
    global _shared_http
    if _shared_http is None or _shared_http.is_closed:
        _shared_http = httpx.AsyncClient(timeout=30.0)
    return _shared_http


async def search_web(query: str, max_results: int = 5,
                     depth: str = "basic", topic: str = "general") -> dict:
    """Search the web. Returns structured results for the Brain to reason about."""
    http = _get_http_client()

    if settings.TAVILY_API_KEY:
        try:
            r = await http.post("https://api.tavily.com/search", json={
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": depth,
                "include_answer": True,
                "topic": topic,
            })
            r.raise_for_status()
            data = r.json()
            return {
                "answer": data.get("answer"),
                "results": [
                    {"title": x.get("title", ""), "url": x.get("url", ""),
                     "content": x.get("content", ""), "score": x.get("score", 0)}
                    for x in data.get("results", [])
                ],
                "backend": "tavily",
            }
        except Exception as e:
            pass  # Fall through to SearXNG

    # SearXNG fallback
    try:
        r = await http.get(f"{settings.SEARXNG_URL}/search",
                        params={"q": query, "format": "json"})
        r.raise_for_status()
        data = r.json()
        return {
            "answer": None,
            "results": [
                {"title": x.get("title", ""), "url": x.get("url", ""),
                 "content": x.get("content", "")}
                for x in data.get("results", [])[:max_results]
            ],
            "backend": "searxng",
        }
    except Exception:
        pass

    return {"answer": None, "results": [], "backend": "none",
            "error": "No search backend configured (set TAVILY_API_KEY in .env)"}
