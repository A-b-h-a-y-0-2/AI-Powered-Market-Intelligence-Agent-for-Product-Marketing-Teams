"""SEC EDGAR full-text search tool.

Queries the EDGAR full-text search API for 8-K filings that mention
a given company name. Returns CrawlResult objects so the existing
ExtractionAgent pipeline processes them without modification.

API is completely free — no key required. Rate limit: 10 req/sec.
User-Agent header is required by EDGAR's fair-use policy.

Useful events captured:
- Acquisitions (items 1.01, 2.01)
- Material agreements / partnerships (item 1.01)
- Results of operations / earnings (item 2.02)
- Leadership changes (item 5.02)
- Press releases / news (item 8.01, EX-99)

Private companies (McKinsey, BCG, Bain) won't file directly, but
EDGAR captures filings from companies that MENTION them — e.g.
a client filing that discloses hiring McKinsey as strategic advisor,
or an acquired company's filing that names BCG.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("edgar_tool")

_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_USER_AGENT = "MarketIntelligenceAgent research@marketintel.internal"

# 8-K item codes that signal strategic events worth monitoring
_STRATEGIC_ITEMS = {
    "1.01": "material agreement or partnership",
    "2.01": "acquisition or disposition of assets",
    "2.02": "results of operations",
    "5.02": "leadership change",
    "8.01": "other material event / press release",
    "9.01": "financial statements and exhibits",
}


class EDGARTool:
    """Searches EDGAR for 8-K filings mentioning a company and returns CrawlResults."""

    def __init__(self, timeout_seconds: float = 30.0, max_results: int = 15) -> None:
        self._timeout = timeout_seconds
        self._max_results = max_results

    async def search_8k(
        self,
        company_name: str,
        days: int = 30,
    ) -> list[CrawlResult]:
        """Return CrawlResult objects for recent 8-K filings mentioning company_name."""
        now = datetime.now(tz=timezone.utc)
        start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        params = {
            "q": f'"{company_name}"',
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _SEARCH_URL,
                    params=params,
                    headers={"User-Agent": _USER_AGENT},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error(
                "edgar_search_failed",
                company=company_name,
                error=str(exc),
            )
            return []

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)

        log.info(
            "edgar_search_complete",
            company=company_name,
            total_hits=total,
            returning=min(len(hits), self._max_results),
        )

        results: list[CrawlResult] = []
        for hit in hits[: self._max_results]:
            result = self._hit_to_crawl_result(hit["_source"], company_name)
            if result:
                results.append(result)

        return results

    def _hit_to_crawl_result(
        self, source: dict, queried_company: str
    ) -> CrawlResult | None:
        adsh = source.get("adsh", "")
        if not adsh:
            return None

        ciks = source.get("ciks", [])
        cik_int = ciks[0].lstrip("0") if ciks else ""
        adsh_nodash = adsh.replace("-", "")

        filing_url = (
            f"{_ARCHIVES_BASE}/{cik_int}/{adsh_nodash}/{adsh}-index.htm"
            if cik_int
            else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={adsh}"
        )

        display_names = source.get("display_names", [])
        file_date = source.get("file_date", "")
        file_description = source.get("file_description", "No description provided")
        form = source.get("form", "8-K")
        items = source.get("items", [])

        strategic_signals = [
            f"{item}: {_STRATEGIC_ITEMS[item]}"
            for item in items
            if item in _STRATEGIC_ITEMS
        ]

        content = f"""SEC EDGAR Filing: {form}
Filing Date: {file_date}
Filing Company: {', '.join(display_names)}
Document: {file_description}
Strategic Signals: {', '.join(strategic_signals) if strategic_signals else 'General filing'}
Mentions: {queried_company}
Source: SEC EDGAR (public regulatory filing)
Accession Number: {adsh}
Full Filing: {filing_url}

This filing mentions {queried_company}. Review the full document at {filing_url} for complete details.
"""

        content_hash = hashlib.sha256(adsh.encode()).hexdigest()

        return CrawlResult(
            url=filing_url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=content_hash,
            status_code=200,
            is_changed=True,
            company=queried_company,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _SEARCH_URL,
                    params={"q": '"Accenture"', "forms": "8-K", "dateRange": "custom",
                            "startdt": "2026-01-01", "enddt": "2026-06-24"},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
