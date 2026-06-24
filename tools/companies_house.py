"""UK Companies House API tool.

Provides access to UK company filings, financial accounts, and director changes.
Critical for monitoring private firms (McKinsey, BCG, Bain) via their UK subsidiaries,
which must file accounts with Companies House unlike their US parent entities.

API: https://api.company-information.service.gov.uk/
Auth: Free API key — register at https://developer.company-information.service.gov.uk/
Rate limit: 600 req/5 min on free tier.

Known company numbers:
  McKinsey & Company Limited              → 01327109
  The Boston Consulting Group (UK) Ltd    → 01277015
  Bain and Company Inc (UK)               → 01382593
  Deloitte LLP                            → OC303675
  KPMG LLP                                → OC301540
  Oliver Wyman Limited                    → 02997789
  Accenture (UK) Limited                  → 04937819
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("companies_house_tool")

_BASE_URL = "https://api.company-information.service.gov.uk"

# Pre-mapped UK company numbers for tracked firms — avoids name ambiguity
_COMPANY_NUMBERS: dict[str, str] = {
    "mckinsey": "01327109",
    "boston consulting group": "01277015",
    "bcg": "01277015",
    "bain": "01382593",
    "deloitte": "OC303675",
    "kpmg": "OC301540",
    "oliver wyman": "02997789",
    "accenture": "04937819",
}


class CompaniesHouseTool:
    """Fetches UK Companies House filings and financial data."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        max_filings: int = 5,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_filings = max_filings

    def _auth_header(self) -> dict[str, str]:
        encoded = base64.b64encode(f"{self._api_key}:".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    async def search_company(
        self,
        company_name: str,
        days: int = 180,
    ) -> list[CrawlResult]:
        if not self._api_key:
            log.warning(
                "companies_house_skipped",
                reason="COMPANIES_HOUSE_API_KEY not configured",
                company=company_name,
            )
            return []

        company_number = self._resolve_company_number(company_name)
        if not company_number:
            company_number = await self._search_for_number(company_name)
        if not company_number:
            log.warning(
                "companies_house_no_match",
                company=company_name,
            )
            return []

        results: list[CrawlResult] = []
        profile = await self._get_profile(company_number, company_name)
        if profile:
            results.append(profile)
        filings = await self._get_recent_filings(company_number, company_name, days)
        results.extend(filings)

        log.info(
            "companies_house_complete",
            company=company_name,
            company_number=company_number,
            results=len(results),
        )
        return results

    def _resolve_company_number(self, company_name: str) -> str | None:
        name_lower = company_name.lower()
        for key, number in _COMPANY_NUMBERS.items():
            if key in name_lower:
                return number
        return None

    async def _search_for_number(self, company_name: str) -> str | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/search/companies",
                    params={"q": company_name, "items_per_page": 3},
                    headers=self._auth_header(),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if items:
                    return items[0].get("company_number")
        except httpx.HTTPError as exc:
            log.error("companies_house_search_failed", company=company_name, error=str(exc))
        return None

    async def _get_profile(
        self, company_number: str, company_name: str
    ) -> CrawlResult | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/company/{company_number}",
                    headers=self._auth_header(),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error(
                "companies_house_profile_failed",
                company=company_name,
                number=company_number,
                error=str(exc),
            )
            return None

        company_status = data.get("company_status", "unknown")
        reg_address = data.get("registered_office_address", {})
        address_str = ", ".join(
            v for v in [
                reg_address.get("premises"),
                reg_address.get("address_line_1"),
                reg_address.get("locality"),
                reg_address.get("postal_code"),
            ] if v
        )
        accounts = data.get("accounts", {})
        last_accounts = accounts.get("last_accounts", {})
        next_due = accounts.get("next_due", "")
        incorporated = data.get("date_of_creation", "")
        company_type = data.get("type", "")
        jurisdiction = data.get("jurisdiction", "united-kingdom")

        url = f"https://find-and-update.company-information.service.gov.uk/company/{company_number}"
        content = f"""Companies House Profile: {data.get('company_name', company_name)}
Company Number: {company_number}
Status: {company_status}
Type: {company_type}
Jurisdiction: {jurisdiction}
Incorporated: {incorporated}
Registered Address: {address_str}
Last Accounts Period: {last_accounts.get('period_end_on', 'unknown')} ({last_accounts.get('type', 'unknown')})
Next Accounts Due: {next_due}
Source: UK Companies House (public registry)

This profile shows the UK legal entity for {company_name}.
Filing history and financial accounts available at {url}
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"ch_profile_{company_number}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company_name,
        )

    async def _get_recent_filings(
        self, company_number: str, company_name: str, days: int
    ) -> list[CrawlResult]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/company/{company_number}/filing-history",
                    params={
                        "items_per_page": self._max_filings,
                        "category": "accounts,annual-return,confirmation-statement,mortgage,officers",
                    },
                    headers=self._auth_header(),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error(
                "companies_house_filings_failed",
                company=company_name,
                number=company_number,
                error=str(exc),
            )
            return []

        items = data.get("items", [])
        results: list[CrawlResult] = []
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for item in items:
            filed_at = item.get("date", "")
            try:
                filed_dt = datetime.fromisoformat(filed_at).replace(tzinfo=timezone.utc)
                if filed_dt < cutoff:
                    continue
            except ValueError:
                pass

            transaction_id = item.get("transaction_id", "")
            category = item.get("category", "")
            description = item.get("description", "Filing")
            filing_type = item.get("type", "")
            doc_url = f"https://find-and-update.company-information.service.gov.uk/company/{company_number}/filing-history/{transaction_id}/document?format=webreader" if transaction_id else f"https://find-and-update.company-information.service.gov.uk/company/{company_number}/filing-history"

            content = f"""Companies House Filing: {description}
Company: {company_name} (UK entity #{company_number})
Filing Date: {filed_at}
Category: {category}
Type: {filing_type}
Transaction ID: {transaction_id}
Source: UK Companies House (public filing registry)

{company_name} filed a "{description}" ({filing_type}) with UK Companies House on {filed_at}.
Category: {category}. Full document: {doc_url}
"""

            results.append(CrawlResult(
                url=doc_url,
                content=content,
                crawl_timestamp=now_iso,
                content_hash=hashlib.sha256(f"ch_{transaction_id}".encode()).hexdigest(),
                status_code=200,
                is_changed=True,
                company=company_name,
            ))

        return results

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/company/04937819",
                    headers=self._auth_header(),
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
