"""CourtListener litigation intelligence tool.

Searches PACER/CourtListener for federal court cases involving target companies.
Litigation signals: IP disputes reveal technology bets, antitrust = market dominance,
employment cases = internal culture signals, contract disputes = failed partnerships.

API: https://www.courtlistener.com/api/rest/v4/
Auth: Free token required — register at https://www.courtlistener.com/sign-in/
      then go to Profile → API Token. 30,000 req/day.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("court_listener_tool")

_BASE_URL = "https://www.courtlistener.com/api/rest/v4"

_NATURE_OF_SUIT_LABELS: dict[str, str] = {
    "110": "Insurance",
    "190": "Contract",
    "196": "Franchise",
    "230": "Rent/Lease",
    "315": "Airplane Product Liability",
    "370": "Other Fraud",
    "380": "Other Personal Property",
    "440": "Other Civil Rights",
    "470": "Racketeer",
    "480": "Consumer Credit",
    "830": "Patent",
    "840": "Trademark",
    "850": "Securities/Commodities",
    "890": "Other Statutory Actions",
    "950": "Constitutional State Statutes",
}


class CourtListenerTool:
    """Searches CourtListener for federal court cases involving target companies."""

    def __init__(
        self,
        api_token: str | None = None,
        timeout_seconds: float = 20.0,
        max_results: int = 10,
    ) -> None:
        self._token = api_token
        self._timeout = timeout_seconds
        self._max_results = max_results

    def _headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Token {self._token}"}
        return {}

    async def search_cases(
        self,
        company_name: str,
        days: int = 180,
    ) -> list[CrawlResult]:
        if not self._token:
            log.warning(
                "court_listener_skipped",
                reason="COURT_LISTENER_TOKEN not configured — free token at courtlistener.com/sign-in",
                company=company_name,
            )
            return []

        now = datetime.now(tz=timezone.utc)
        filed_after = (now - timedelta(days=days)).strftime("%Y-%m-%d")

        params = {
            "q": f'"{company_name}"',
            "filed_after": filed_after,
            "order_by": "-date_filed",
            "page_size": self._max_results,
            "format": "json",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/dockets/",
                    params=params,
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("court_listener_search_failed", company=company_name, error=str(exc))
            return []

        data = resp.json()
        dockets = data.get("results", [])
        total = data.get("count", 0)

        log.info(
            "court_listener_search_complete",
            company=company_name,
            total=total,
            returning=len(dockets),
        )

        results: list[CrawlResult] = []
        now_iso = now.isoformat()
        for docket in dockets:
            result = self._docket_to_crawl_result(docket, company_name, now_iso)
            if result:
                results.append(result)
        return results

    def _docket_to_crawl_result(
        self, docket: dict, company: str, now_iso: str
    ) -> CrawlResult | None:
        docket_id = str(docket.get("id", ""))
        if not docket_id:
            return None

        case_name = docket.get("case_name", "Unknown Case")
        date_filed = docket.get("date_filed", "")
        date_terminated = docket.get("date_terminated", "")
        court = docket.get("court", "")
        cause = docket.get("cause", "")
        nature_raw = str(docket.get("nature_of_suit", ""))
        nature = _NATURE_OF_SUIT_LABELS.get(nature_raw, nature_raw)
        docket_number = docket.get("docket_number", "")
        assigned_to = docket.get("assigned_to_str", "")

        status = "Active" if not date_terminated else f"Terminated {date_terminated}"

        url = f"https://www.courtlistener.com/docket/{docket_id}/"

        content = f"""Federal Court Case: {case_name}
Docket Number: {docket_number}
Court: {court}
Date Filed: {date_filed}
Status: {status}
Cause of Action: {cause}
Nature of Suit: {nature} (Code: {nature_raw})
Assigned To: {assigned_to}
Source: CourtListener / PACER (Federal court database)

Strategic Signal: {company} involved in federal case "{case_name}" filed {date_filed}.
Case type: {nature} — {cause}
Case details: {url}
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=now_iso,
            content_hash=hashlib.sha256(f"cl_{docket_id}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/dockets/",
                    params={"q": '"Accenture"', "page_size": 1, "format": "json"},
                    headers=self._headers(),
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
