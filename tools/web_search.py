"""Tavily web search tool.

Thin wrapper around the Tavily API that returns normalised result dicts the
Researcher agent can turn into Claims. Member 1 (Vinayak Paka).
"""
from __future__ import annotations

from config import TAVILY_API_KEY

_client = None


def _get_client():
    global _client
    if _client is None:
        if not TAVILY_API_KEY:
            raise RuntimeError(
                "TAVILY_API_KEY is not set. Add it to your .env file "
                "(get a free key at https://tavily.com)."
            )
        # Imported lazily so the rest of the project runs without the dependency.
        from tavily import TavilyClient

        _client = TavilyClient(api_key=TAVILY_API_KEY)
    return _client


def web_search(
    query: str, max_results: int = 4, search_depth: str = "basic"
) -> list[dict]:
    """Search the web for ``query``.

    ``search_depth`` is ``"basic"`` or ``"advanced"`` — the Researcher uses
    ``"advanced"`` on retries to dig up evidence a basic search missed.
    Returns a list of ``{"title", "url", "content"}`` dicts.
    """
    resp = _get_client().search(
        query=query, max_results=max_results, search_depth=search_depth
    )
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in resp.get("results", [])
    ]
