"""Market research adapter.

Live: Tavily Search API (POST https://api.tavily.com/search, `Authorization: Bearer tvly-...`;
response `results[]` with `title`, `url`, `content`, `score`). Mock: clearly labelled sample data per project
type, so the price step still has something to cite offline. A failed live call falls back to the samples
with a visible warning instead of breaking the flow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

import httpx

from .. import config
from ..models import Source
from . import http
from .http import AdapterError

PROVIDER = "Tavily"


@dataclass
class SearchResult:
    sources: List[Source]
    mode: str  # live | mock | fallback
    ms: int
    queries: List[str]
    warning: str = ""
    credits: int = 0
    answer: str = ""


def _describe_error(resp: httpx.Response) -> str:
    try:
        d = resp.json().get("detail")
    except (ValueError, AttributeError):
        return resp.text[:200]
    if isinstance(d, dict):
        return str(d.get("error", d))[:200]
    return str(d)[:200]


MOCK_SNIPPETS: Dict[str, List[tuple]] = {
    "ios": [("Sample: iOS freelance rate survey (mock)", "Independent iOS developers typically bill $55 to $110 per hour; SwiftUI specialists sit at the upper end."),
            ("Sample: app development cost guide (mock)", "A small native iOS MVP with accounts and payments commonly lands between $8,000 and $25,000."),
            ("Sample: marketplace price guide (mock)", "Mid-level mobile freelancers on marketplaces quote $40-$75/h; agencies quote 2-3x that.")],
    "mobile": [("Sample: cross-platform rate survey (mock)", "Flutter and React Native freelancers usually charge $45 to $95 per hour."),
               ("Sample: mobile app cost guide (mock)", "A cross-platform MVP with login, feeds and push typically costs $7,000 to $20,000."),
               ("Sample: marketplace price guide (mock)", "Senior mobile contractors quote $70-$120/h for apps with payments.")],
    "web": [("Sample: web developer rate survey (mock)", "Freelance full-stack web developers typically charge $50 to $100 per hour."),
            ("Sample: SaaS MVP cost guide (mock)", "A small web MVP with auth, dashboard and billing commonly lands between $6,000 and $18,000."),
            ("Sample: agency vs freelancer comparison (mock)", "Agencies quote 2-3x freelancer rates; independent seniors charge $60-$120/h.")],
    "automation": [("Sample: automation freelancer rates (mock)", "n8n, Zapier and Make.com specialists usually bill $40 to $90 per hour."),
                   ("Sample: workflow automation price guide (mock)", "Typical small business automations (CRM sync, lead routing) are priced $500 to $3,000 per workflow."),
                   ("Sample: marketplace price guide (mock)", "Experienced automation consultants quote $60-$110/h for multi-system integrations.")],
    "design": [("Sample: brand designer rate survey (mock)", "Freelance logo and brand identity designers typically charge $40 to $100 per hour."),
               ("Sample: logo design price guide (mock)", "A logo with a basic brand guide usually costs $800 to $3,500 from an independent designer."),
               ("Sample: marketplace price guide (mock)", "Entry-level designers quote $25-$45/h; senior brand designers $75-$150/h.")],
    "data": [("Sample: data engineering freelance rates (mock)", "Freelance Python scraping and data engineers typically bill $45 to $95 per hour."),
             ("Sample: web scraping project cost guide (mock)", "A maintained scraper for a few sites with scheduled exports costs $1,000 to $5,000."),
             ("Sample: marketplace price guide (mock)", "Scraping specialists quote $35-$80/h; anti-bot and proxy work is billed extra.")],
}


class TavilySearch:
    mode = "live"

    def __init__(self) -> None:
        self.s = config.get_settings()

    def _one(self, query: str, max_results: int) -> Dict[str, Any]:
        body = {"query": query, "search_depth": self.s.tavily_search_depth, "topic": "general",
                "max_results": max_results, "include_answer": False, "include_raw_content": False, "include_usage": True}
        r = http.request(PROVIDER, "POST", self.s.tavily_base_url.rstrip("/") + "/search", timeout_s=self.s.search_timeout_s,
                         retries=2, headers={"Authorization": f"Bearer {self.s.tavily_api_key}"}, json=body,
                         describe_error=_describe_error)
        try:
            return r.json()
        except ValueError as e:
            raise AdapterError(PROVIDER, "response was not JSON") from e

    def search(self, queries: List[str], max_results: int = 4, project_type: str = "web") -> SearchResult:
        t0 = time.time()
        sources: List[Source] = []
        seen, credits = set(), 0
        try:
            for q in queries:
                j = self._one(q, max_results)
                credits += int((j.get("usage") or {}).get("credits") or 0)
                for x in sorted(j.get("results", []), key=lambda x: -(x.get("score") or 0)):
                    url = x.get("url", "")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    sources.append(Source(title=(x.get("title") or url)[:160], url=url,
                                          snippet=" ".join((x.get("content") or "").split())[:500], score=x.get("score")))
            return SearchResult(sources[: max_results * 2], "live", int((time.time() - t0) * 1000), queries, credits=credits)
        except AdapterError as e:
            if not self.s.fallback_to_mock:
                raise
            res = MockSearch().search(queries, max_results, project_type)
            res.mode, res.ms = "fallback", int((time.time() - t0) * 1000)
            res.warning = f"Tavily search failed ({e.message[:140]}); showing labelled sample sources instead."
            return res


class MockSearch:
    mode = "mock"

    def search(self, queries: List[str], max_results: int = 4, project_type: str = "web") -> SearchResult:
        rows = MOCK_SNIPPETS.get(project_type, MOCK_SNIPPETS["web"])
        src = [Source(title=t, url=f"https://example.com/mock/{project_type}/{i + 1}", snippet=s) for i, (t, s) in enumerate(rows)]
        return SearchResult(src[:max_results], "mock", 0, queries)


def get_search():
    return TavilySearch() if config.get_settings().search_live else MockSearch()
