"""Alpha Vantage stock news and sentiment for Accenture (ACN).

Only useful for Accenture (the one public company in our tracked set).
NEWS_SENTIMENT endpoint returns curated news with pre-computed sentiment scores
and relevance weighting — cleaner than raw RSS for stock-relevant events.

API: https://www.alphavantage.co/documentation/
Auth: Free key at https://www.alphavantage.co/support/#api-key (25 req/day free)

Signals captured:
  - Earnings announcements and guidance
  - Analyst upgrades/downgrades (reveal perception of strategic direction)
  - Partnership and acquisition news (higher relevance score when ACN mentioned)
  - Leadership changes affecting stock
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("alpha_vantage_tool")

_BASE_URL = "https://www.alphavantage.co/query"

# Accenture is the only public company in our tracked set
_TICKER_MAP: dict[str, str] = {
    "accenture": "ACN",
    "accenture strategy": "ACN",
}


class AlphaVantageTool:
    """Pulls news sentiment and stock events for Accenture from Alpha Vantage."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        max_results: int = 20,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_results = max_results

    def _get_ticker(self, company_name: str) -> str | None:
        name_lower = company_name.lower()
        for key, ticker in _TICKER_MAP.items():
            if key in name_lower:
                return ticker
        return None

    async def search_news(
        self,
        company_name: str,
        days: int = 14,
    ) -> list[CrawlResult]:
        if not self._api_key:
            log.warning(
                "alpha_vantage_skipped",
                reason="ALPHA_VANTAGE_API_KEY not configured",
                company=company_name,
            )
            return []

        ticker = self._get_ticker(company_name)
        if not ticker:
            log.info(
                "alpha_vantage_no_ticker",
                company=company_name,
                message="Only Accenture (ACN) is tracked as a public company",
            )
            return []

        now = datetime.now(tz=timezone.utc)
        from_dt = now - timedelta(days=days)
        time_from = from_dt.strftime("%Y%m%dT%H%M")

        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "time_from": time_from,
            "limit": self._max_results,
            "sort": "RELEVANCE",
            "apikey": self._api_key,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _BASE_URL, params=params, timeout=self._timeout
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("alpha_vantage_failed", company=company_name, error=str(exc))
            return []

        data = resp.json()

        if "Information" in data:
            log.warning("alpha_vantage_rate_limited", message=data["Information"])
            return []

        feed = data.get("feed", [])
        log.info(
            "alpha_vantage_complete",
            company=company_name,
            ticker=ticker,
            articles=len(feed),
        )

        results: list[CrawlResult] = []
        now_iso = now.isoformat()
        for article in feed:
            result = self._article_to_crawl_result(article, company_name, ticker, now_iso)
            if result:
                results.append(result)
        return results

    def _article_to_crawl_result(
        self, article: dict, company: str, ticker: str, now_iso: str
    ) -> CrawlResult | None:
        url = article.get("url", "")
        if not url:
            return None

        title = article.get("title", "")
        summary = article.get("summary", "")
        source = article.get("source", "")
        time_published = article.get("time_published", "")
        overall_sentiment = article.get("overall_sentiment_label", "Neutral")
        sentiment_score = article.get("overall_sentiment_score", 0.0)

        # Find this ticker's specific sentiment
        ticker_sentiments = article.get("ticker_sentiment", [])
        ticker_data = next(
            (t for t in ticker_sentiments if t.get("ticker") == ticker), {}
        )
        relevance_score = float(ticker_data.get("relevance_score", 0))
        ticker_sentiment_label = ticker_data.get("ticker_sentiment_label", overall_sentiment)

        if relevance_score < 0.15:
            return None  # Article barely mentions our company

        content = f"""Financial News: {title}
Source: {source}
Published: {time_published}
Sentiment: {ticker_sentiment_label} (score: {sentiment_score:.2f})
Relevance to {ticker}: {relevance_score:.2f}
Company: {company} ({ticker})
Source Type: Alpha Vantage News Sentiment API

Summary: {summary}

Strategic Signal: Stock-relevant news about {company} — "{title}".
Sentiment: {ticker_sentiment_label}. Relevance: {relevance_score:.2f}/1.0
Analyst and investor news reveals market perception of {company}'s strategic direction.
Full article: {url}
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=now_iso,
            content_hash=hashlib.sha256(url.encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company,
        )

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _BASE_URL,
                    params={
                        "function": "NEWS_SENTIMENT",
                        "tickers": "ACN",
                        "limit": 1,
                        "apikey": self._api_key,
                    },
                    timeout=10.0,
                )
                data = resp.json()
                return "feed" in data or "Information" in data
        except Exception:
            return False
