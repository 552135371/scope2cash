"""Market research adapter. Live: Tavily Search API. Mock: clearly-labelled sample data."""
from __future__ import annotations

from typing import List, Protocol

import httpx

from ..config import settings
from ..models import Source


class SearchClient(Protocol):
    mode: str

    def search(self, query: str, max_results: int = 5) -> List[Source]: ...


class TavilySearch:
    mode = "live"

    def search(self, query: str, max_results: int = 5) -> List[Source]:
        r = httpx.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
            timeout=30,
        )
        r.raise_for_status()
        return [Source(title=x.get("title", ""), url=x.get("url", ""), snippet=(x.get("content") or "")[:400])
                for x in r.json().get("results", [])]


class MockSearch:
    mode = "mock"

    def search(self, query: str, max_results: int = 5) -> List[Source]:
        samples = [
            ("Sample: freelance rate survey (mock)", "Typical freelance rates for this kind of work range from $40 to $95 per hour depending on seniority."),
            ("Sample: marketplace price guide (mock)", "Small fixed-price projects of this scope commonly land between $1,500 and $6,000."),
            ("Sample: agency vs freelancer comparison (mock)", "Agencies quote 2-3x freelancer rates; independent seniors charge $60-$120/h."),
        ]
        return [Source(title=t, url="https://example.com/mock-source", snippet=s) for t, s in samples[:max_results]]


def get_search() -> SearchClient:
    return TavilySearch() if settings.search_live else MockSearch()
