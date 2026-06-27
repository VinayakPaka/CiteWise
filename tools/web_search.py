"""Tavily web search tool.

Thin wrapper around the Tavily API that returns normalised result dicts the
Researcher agent can turn into Claims. Member 1 (Vinayak Paka).

Results are passed through the source-quality gate (``tools.source_quality``):
non-citable domains (social media, video, forums, and — under the default strict
policy — tertiary wikis like Wikipedia) are dropped, and the survivors are ranked
so authoritative primary sources surface first. This is why a low-quality source
such as a Facebook post or YouTube video can no longer reach the final report.
"""
from __future__ import annotations

from config import TAVILY_API_KEY
from tools.source_quality import blocked_domains, domain_score, is_citable

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


def _raw_search(client, query: str, max_results: int, search_depth: str) -> dict:
    """Call Tavily, asking it to exclude denied domains where the SDK supports it."""
    kwargs = dict(query=query, max_results=max_results, search_depth=search_depth)
    try:
        return client.search(exclude_domains=blocked_domains(), **kwargs)
    except TypeError:
        # Older tavily-python without exclude_domains — the client-side filter
        # below is the real guarantee, so degrade gracefully.
        return client.search(**kwargs)


def web_search(
    query: str, max_results: int = 4, search_depth: str = "basic"
) -> list[dict]:
    """Search the web for ``query``, returning only citable, authority-ranked results.

    ``search_depth`` is ``"basic"`` or ``"advanced"`` — the Researcher uses
    ``"advanced"`` on retries to dig up evidence a basic search missed. We
    over-fetch, drop non-citable sources (social media, video, forums, wikis),
    then sort the survivors by domain authority so the strongest primary sources
    are kept. Returns up to ``max_results`` ``{"title", "url", "content"}`` dicts.
    """
    # Over-fetch: dropping non-citable hits and keeping only the most authoritative
    # would otherwise leave us short of max_results strong sources.
    fetch_n = max(max_results * 3, 10)
    resp = _raw_search(_get_client(), query, fetch_n, search_depth)

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in resp.get("results", [])
        if is_citable(r.get("url", ""))
    ]
    # Stable sort by descending authority; ties keep Tavily's relevance order.
    results.sort(key=lambda r: domain_score(r["url"]), reverse=True)
    return results[:max_results]
