"""Semantic Scholar research paper intelligence tool.

Monitors academic publications authored by researchers at target firms.
Research papers are 6-18 month leading indicators of AI/tech product direction.
McKinsey Global Institute, BCG Henderson Institute, Accenture Labs, Deloitte Insights
all publish research that precedes product launches.

API: https://api.semanticscholar.org/graph/v1/
Auth: No key needed for basic queries (100 req/sec).
      Optional API key at https://www.semanticscholar.org/product/api for higher limits.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("semantic_scholar_tool")

_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# Institution search terms for each tracked firm
_INSTITUTION_QUERIES: dict[str, list[str]] = {
    "mckinsey": ["McKinsey Global Institute", "McKinsey & Company", "QuantumBlack"],
    "boston consulting group": ["BCG Henderson Institute", "Boston Consulting Group", "BCG Gamma"],
    "bcg": ["BCG Henderson Institute", "BCG Gamma"],
    "bain": ["Bain & Company"],
    "deloitte": ["Deloitte Insights", "Deloitte AI Institute"],
    "kpmg": ["KPMG"],
    "oliver wyman": ["Oliver Wyman"],
    "accenture": ["Accenture Labs", "Accenture Research", "Accenture Technology Labs"],
}

_PAPER_FIELDS = "title,authors,abstract,year,venue,publicationDate,externalIds,citationCount"


class SemanticScholarTool:
    """Searches Semantic Scholar for research papers from target institutions."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        max_results: int = 10,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_results = max_results

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"x-api-key": self._api_key}
        return {}

    def _get_search_terms(self, company_name: str) -> list[str]:
        name_lower = company_name.lower()
        for key, terms in _INSTITUTION_QUERIES.items():
            if key in name_lower:
                return terms
        return [company_name]

    async def search_papers(
        self,
        company_name: str,
        days: int = 365,
    ) -> list[CrawlResult]:
        since_year = (datetime.now(tz=timezone.utc) - timedelta(days=days)).year
        search_terms = self._get_search_terms(company_name)

        all_results: list[CrawlResult] = []
        seen_ids: set[str] = set()

        for i, term in enumerate(search_terms):
            if i > 0:
                await asyncio.sleep(1.5)  # avoid 429 on burst of sequential queries
            papers = await self._search_term(term, since_year, company_name)
            for p in papers:
                paper_id = hashlib.sha256(p.url.encode()).hexdigest()
                if paper_id not in seen_ids:
                    seen_ids.add(paper_id)
                    all_results.append(p)

        log.info(
            "semantic_scholar_search_complete",
            company=company_name,
            terms=search_terms,
            total=len(all_results),
        )
        return all_results[: self._max_results]

    async def _search_term(
        self, query: str, since_year: int, company: str
    ) -> list[CrawlResult]:
        params = {
            "query": query,
            "fields": _PAPER_FIELDS,
            "limit": min(self._max_results, 20),
            "year": f"{since_year}-",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _SEARCH_URL,
                    params=params,
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error(
                "semantic_scholar_failed",
                query=query,
                company=company,
                error=str(exc),
            )
            return []

        data = resp.json()
        papers = data.get("data", [])
        results: list[CrawlResult] = []
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for paper in papers:
            result = self._paper_to_crawl_result(paper, company, now_iso)
            if result:
                results.append(result)
        return results

    def _paper_to_crawl_result(
        self, paper: dict, company: str, now_iso: str
    ) -> CrawlResult | None:
        paper_id = paper.get("paperId", "")
        if not paper_id:
            return None

        title = paper.get("title", "Untitled Paper")
        abstract = paper.get("abstract", "") or ""
        year = paper.get("year", "")
        venue = paper.get("venue", "") or ""
        pub_date = paper.get("publicationDate", "") or str(year)
        citations = paper.get("citationCount", 0)

        authors = paper.get("authors", [])
        author_names = [a.get("name", "") for a in authors[:5] if a.get("name")]

        external_ids = paper.get("externalIds", {})
        doi = external_ids.get("DOI", "")
        arxiv = external_ids.get("ArXiv", "")

        url = f"https://www.semanticscholar.org/paper/{paper_id}"
        if doi:
            source_url = f"https://doi.org/{doi}"
        elif arxiv:
            source_url = f"https://arxiv.org/abs/{arxiv}"
        else:
            source_url = url

        content = f"""Research Paper: {title}
Authors: {', '.join(author_names)}
Published: {pub_date}
Venue: {venue}
Citations: {citations}
DOI: {doi or 'N/A'}
ArXiv: {arxiv or 'N/A'}
Source: Semantic Scholar (Academic paper database)

Abstract: {abstract[:600]}

Strategic Signal: Researchers affiliated with {company} published "{title}" ({pub_date}).
High-citation papers often precede product launches by 6-18 months.
Read at: {source_url}
"""

        return CrawlResult(
            url=source_url,
            content=content,
            crawl_timestamp=now_iso,
            content_hash=hashlib.sha256(paper_id.encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _SEARCH_URL,
                    params={"query": "Accenture Labs AI", "fields": "title", "limit": 1},
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
