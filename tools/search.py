"""Tavily search tool for live research and KB-miss fallback.

Used in two contexts:
1. Discovery: find recent news/announcements for tracked companies
2. Fallback: answer queries when KB coverage is insufficient

Always has timeout + retry. Raises SearchError with named codes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from observability.logger import get_logger
from tools.errors import AgentError, ErrorCode

log = get_logger("search")


class SearchResult:
    """Single Tavily search result."""

    __slots__ = ("title", "url", "content", "score", "published_date")

    def __init__(
        self,
        title: str,
        url: str,
        content: str,
        score: float,
        published_date: str | None = None,
    ) -> None:
        self.title = title
        self.url = url
        self.content = content
        self.score = score
        self.published_date = published_date

    def __repr__(self) -> str:
        return f"SearchResult(title={self.title!r}, url={self.url!r}, score={self.score:.2f})"


class SearchError(AgentError):
    """Raised when Tavily search fails."""


class TavilySearch:
    """Async wrapper around Tavily search API.

    All calls have explicit timeouts and exponential backoff on transient failures.
    Dependency-injected: never instantiates its own HTTP clients internally without injection.
    """

    _BASE_URL = "https://api.tavily.com"
    _DEFAULT_TIMEOUT = 30.0
    _MAX_RESULTS_DEFAULT = 5

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self._BASE_URL,
            timeout=httpx.Timeout(self._timeout),
            headers={"Content-Type": "application/json"},
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "TavilySearch":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()

    @retry(
        retry=retry_if_exception_type(SearchError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def search(
        self,
        query: str,
        max_results: int = _MAX_RESULTS_DEFAULT,
        search_depth: str = "advanced",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        days: int | None = None,
        topic: str = "news",
    ) -> list[SearchResult]:
        """Run a Tavily search in research mode and return structured results.

        Defaults to research mode: search_depth="advanced", topic="news",
        include_answer="advanced" (Tavily synthesises an LLM answer from results).

        Args:
            query: The search query string.
            max_results: Maximum number of results (1–10).
            search_depth: "advanced" (thorough, default) or "basic" (fast).
            include_domains: Restrict results to these domains.
            exclude_domains: Exclude results from these domains.
            days: Only return results from the last N days (freshness filter).
            topic: "news" (default) or "general" or "finance".

        Raises:
            SearchError: On API failure, timeout, or invalid response.
        """
        if not self._client:
            raise SearchError(
                code=ErrorCode.SEARCH_UNAVAILABLE,
                message="TavilySearch client not connected. Call connect() first.",
            )

        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": min(max_results, 10),
            "search_depth": search_depth,
            "topic": topic,
            "include_answer": "advanced",  # Tavily synthesises a research answer
            "include_raw_content": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        if days is not None:
            payload["days"] = days

        log.info(
            "search_started",
            agent="search",
            action="tavily_search",
            query=query[:100],
            max_results=max_results,
        )

        try:
            response = await self._client.post("/search", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SearchError(
                code=ErrorCode.SEARCH_TIMEOUT,
                message=f"Tavily search timed out after {self._timeout}s",
                cause=exc,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise SearchError(
                    code=ErrorCode.SEARCH_RATE_LIMITED,
                    message="Tavily rate limit exceeded",
                    cause=exc,
                ) from exc
            raise SearchError(
                code=ErrorCode.SEARCH_FAILED,
                message=f"Tavily API returned HTTP {status}",
                cause=exc,
            ) from exc
        except httpx.RequestError as exc:
            raise SearchError(
                code=ErrorCode.SEARCH_FAILED,
                message=f"Tavily request error: {exc}",
                cause=exc,
            ) from exc

        try:
            data = response.json()
            results = data.get("results", [])
            synthesised_answer = data.get("answer")  # present when include_answer="advanced"
        except Exception as exc:
            raise SearchError(
                code=ErrorCode.SEARCH_FAILED,
                message="Failed to parse Tavily response JSON",
                cause=exc,
            ) from exc

        parsed = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                # Prefer the synthesised answer as the first result's content when available
                content=synthesised_answer or r.get("content", "") if i == 0 else r.get("content", ""),
                score=float(r.get("score", 0.0)),
                published_date=r.get("published_date"),
            )
            for i, r in enumerate(results)
            if r.get("url")
        ]

        log.info(
            "search_completed",
            agent="search",
            action="tavily_search",
            query=query[:100],
            results_returned=len(parsed),
            status="ok",
        )
        return parsed

    async def search_company_news(
        self,
        company: str,
        topic: str | None = None,
        days: int = 30,
        max_results: int = 5,
    ) -> list[SearchResult]:
        """Convenience wrapper for tracking a specific company's news.

        Uses research mode (advanced depth + news topic) for richer results.
        """
        query = f"{company} {topic}" if topic else f"{company} news announcement strategy update"
        return await self.search(
            query=query,
            max_results=max_results,
            search_depth="advanced",
            topic="news",
            days=days,
        )

    async def search_for_fact(
        self,
        company: str,
        fact_type: str,
    ) -> list[SearchResult]:
        """Search for a specific fact about a company (used in KB-miss fallback).

        Args:
            company: The company name.
            fact_type: E.g. "revenue 2026", "pricing plans", "funding round".
        """
        query = f"{company} {fact_type}"
        return await self.search(
            query=query,
            max_results=3,
            search_depth="advanced",
            days=90,
        )

    async def health_check(self) -> dict[str, str]:
        """Verify the Tavily API is reachable."""
        try:
            results = await self.search("test connectivity", max_results=1)
            return {"status": "ok", "results": str(len(results))}
        except SearchError as exc:
            return {"status": "error", "error_code": exc.code, "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "error_code": ErrorCode.SEARCH_FAILED, "message": str(exc)}


# Convenience function for one-off searches without managing connection lifecycle
async def quick_search(
    api_key: str,
    query: str,
    max_results: int = 5,
    days: int | None = None,
) -> list[SearchResult]:
    """One-shot search that manages its own connection."""
    async with TavilySearch(api_key=api_key) as searcher:
        return await searcher.search(query=query, max_results=max_results, days=days)
