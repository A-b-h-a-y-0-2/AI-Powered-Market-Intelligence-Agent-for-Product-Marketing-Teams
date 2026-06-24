"""Lens.org patent search tool.

Searches global patent databases (USPTO, EPO, WIPO) for filings by target companies.
Patent filings are 12-24 month leading indicators of product R&D direction.

API: https://api.lens.org/patent/search
Auth: Bearer token — free registration at https://www.lens.org/lens/user/subscriptions
Rate limit: 500 requests/day on free plan.

Best signals:
  - Accenture Labs / Accenture Technology Labs — prolific patent filer
  - McKinsey QuantumBlack — AI/ML patents
  - BCG Gamma / BCG X — data science patents
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("lens_patents_tool")

_SEARCH_URL = "https://api.lens.org/patent/search"


class LensPatentsTool:
    """Searches Lens.org for patent applications from target companies."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_results: int = 10,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_results = max_results

    async def search_patents(
        self,
        company_name: str,
        days: int = 180,
    ) -> list[CrawlResult]:
        if not self._api_key:
            log.warning(
                "lens_patents_skipped",
                reason="LENS_API_KEY not configured",
                company=company_name,
            )
            return []

        now = datetime.now(tz=timezone.utc)
        from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")

        payload = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"match": {"applicant.name": company_name}},
                                    {"match": {"assignee.name": company_name}},
                                ]
                            }
                        }
                    ],
                    "filter": [
                        {"range": {"date_published": {"gte": from_date}}}
                    ],
                }
            },
            "include": [
                "lens_id",
                "title",
                "date_published",
                "applicant",
                "assignee",
                "inventor",
                "abstract",
                "jurisdiction",
                "doc_number",
                "kind",
                "families",
            ],
            "size": self._max_results,
            "sort": [{"date_published": "desc"}],
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _SEARCH_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self._timeout,
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("lens_search_failed", company=company_name, error=str(exc))
            return []

        data = resp.json()
        patents = data.get("data", [])
        total = data.get("total", 0)

        log.info(
            "lens_search_complete",
            company=company_name,
            total=total,
            returning=len(patents),
        )

        results: list[CrawlResult] = []
        for patent in patents:
            result = self._patent_to_crawl_result(patent, company_name)
            if result:
                results.append(result)
        return results

    def _patent_to_crawl_result(
        self, patent: dict, company: str
    ) -> CrawlResult | None:
        lens_id = patent.get("lens_id", "")
        if not lens_id:
            return None

        title_obj = patent.get("title", [{}])
        title = title_obj[0].get("text", "Untitled Patent") if title_obj else "Untitled Patent"
        date_published = patent.get("date_published", "")
        jurisdiction = patent.get("jurisdiction", "")
        doc_number = patent.get("doc_number", "")

        abstract_list = patent.get("abstract", [{}])
        abstract = abstract_list[0].get("text", "") if abstract_list else ""

        applicants = patent.get("applicant", [])
        applicant_names = [a.get("name", "") for a in applicants if a.get("name")]

        inventors = patent.get("inventor", [])
        inventor_names = [i.get("name", "") for i in inventors[:3] if i.get("name")]

        url = f"https://lens.org/lens/patent/{lens_id}"

        content = f"""Patent Filing: {title}
Applicant(s): {', '.join(applicant_names) or company}
Inventors: {', '.join(inventor_names)}
Published: {date_published}
Jurisdiction: {jurisdiction}
Document Number: {doc_number}
Lens ID: {lens_id}
Source: Lens.org (Global Patent Database — USPTO, EPO, WIPO)

Abstract: {abstract[:600]}

Strategic Signal: {company} filed a patent titled "{title}" on {date_published}.
Patent filings are 12-24 month leading indicators of R&D investment direction.
Full patent: {url}
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(lens_id.encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company,
        )

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _SEARCH_URL,
                    json={"query": {"match": {"applicant.name": "Accenture"}}, "size": 1},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
