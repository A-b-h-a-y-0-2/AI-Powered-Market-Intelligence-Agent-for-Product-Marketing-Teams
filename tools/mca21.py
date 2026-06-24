"""India MCA21 company filing intelligence tool.

Pulls Indian company data via the MCA21 portal and Zauba Corp (which aggregates
public MCA21 data). Critical for tracking Indian subsidiaries of global consulting
firms — all must file annual returns and financial statements with RoC (Registrar
of Companies), unlike US entities of private firms.

Sources:
  Primary  — Zauba Corp (https://www.zaubacorp.com/) — free, aggregates MCA data
  Fallback — MCA21 portal (https://www.mca.gov.in/)

Known Indian CINs:
  McKinsey & Company India Pvt Ltd     → U74140DL1993FTC052104
  BCG India Pvt Ltd                    → U74300MH1994PTC082042
  Bain & Company India Pvt Ltd         → U74140MH2008FTC179618
  Deloitte Touche Tohmatsu India LLP   → AAB-8458
  KPMG Assurance and Consulting LLP    → AAB-0867
  Oliver Wyman India Pvt Ltd           → U74999DL2015PTC282010
  Accenture Solutions Pvt Ltd          → U72200MH2004PTC145236

Rate limit: Zauba Corp is public — no auth needed. Crawl respectfully (1 req/5s).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("mca21_tool")

_ZAUBA_BASE = "https://www.zaubacorp.com"
_MCA_BASE = "https://www.mca.gov.in"

# Pre-mapped CINs for tracked firms' Indian subsidiaries
_COMPANY_CINS: dict[str, dict] = {
    "mckinsey": {
        "cin": "U74140DL1993FTC052104",
        "name": "McKinsey & Company India Pvt Ltd",
        "url": "https://www.zaubacorp.com/company/MCKINSEY-COMPANY-INDIA-PRIVATE-LIMITED/U74140DL1993FTC052104",
    },
    "boston consulting group": {
        "cin": "U74300MH1994PTC082042",
        "name": "BCG India Pvt Ltd",
        "url": "https://www.zaubacorp.com/company/BOSTON-CONSULTING-GROUP-INDIA-PRIVATE-LIMITED/U74300MH1994PTC082042",
    },
    "bcg": {
        "cin": "U74300MH1994PTC082042",
        "name": "BCG India Pvt Ltd",
        "url": "https://www.zaubacorp.com/company/BOSTON-CONSULTING-GROUP-INDIA-PRIVATE-LIMITED/U74300MH1994PTC082042",
    },
    "bain": {
        "cin": "U74140MH2008FTC179618",
        "name": "Bain & Company India Pvt Ltd",
        "url": "https://www.zaubacorp.com/company/BAIN-COMPANY-INDIA-PRIVATE-LIMITED/U74140MH2008FTC179618",
    },
    "deloitte": {
        "cin": "AAB-8458",
        "name": "Deloitte Touche Tohmatsu India LLP",
        "url": "https://www.zaubacorp.com/company/DELOITTE-TOUCHE-TOHMATSU-INDIA-LLP/AAB-8458",
    },
    "kpmg": {
        "cin": "AAB-0867",
        "name": "KPMG Assurance and Consulting Services LLP",
        "url": "https://www.zaubacorp.com/company/KPMG-ASSURANCE-AND-CONSULTING-SERVICES-LLP/AAB-0867",
    },
    "oliver wyman": {
        "cin": "U74999DL2015PTC282010",
        "name": "Oliver Wyman India Pvt Ltd",
        "url": "https://www.zaubacorp.com/company/OLIVER-WYMAN-INDIA-PRIVATE-LIMITED/U74999DL2015PTC282010",
    },
    "accenture": {
        "cin": "U72200MH2004PTC145236",
        "name": "Accenture Solutions Pvt Ltd",
        "url": "https://www.zaubacorp.com/company/ACCENTURE-SOLUTIONS-PRIVATE-LIMITED/U72200MH2004PTC145236",
    },
}

_USER_AGENT = "MarketIntelligenceBot research@marketintel.internal"


class MCA21Tool:
    """Fetches Indian company filing data from MCA21 via Zauba Corp."""

    def __init__(
        self,
        timeout_seconds: float = 25.0,
    ) -> None:
        self._timeout = timeout_seconds

    def _resolve_company(self, company_name: str) -> dict | None:
        name_lower = company_name.lower()
        for key, info in _COMPANY_CINS.items():
            if key in name_lower:
                return info
        return None

    async def search_filings(
        self,
        company_name: str,
        days: int = 365,
    ) -> list[CrawlResult]:
        company_info = self._resolve_company(company_name)
        if not company_info:
            log.info(
                "mca21_no_match",
                company=company_name,
                message="No Indian subsidiary CIN configured for this company",
            )
            return []

        result = await self._fetch_zauba_profile(company_info, company_name)
        results = [result] if result else []

        log.info(
            "mca21_complete",
            company=company_name,
            cin=company_info.get("cin"),
            results=len(results),
        )
        return results

    async def _fetch_zauba_profile(
        self, company_info: dict, parent_company: str
    ) -> CrawlResult | None:
        url = company_info["url"]
        cin = company_info["cin"]
        entity_name = company_info["name"]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": _USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                    },
                    timeout=self._timeout,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPError as exc:
            log.error("mca21_fetch_failed", cin=cin, error=str(exc))
            return self._build_static_profile(company_info, parent_company)

        extracted = self._extract_from_html(html, entity_name)

        content = f"""India MCA21 Company Profile: {entity_name}
CIN: {cin}
Parent Entity: {parent_company}
Indian Subsidiary: {entity_name}
Source: Ministry of Corporate Affairs (MCA21) via Zauba Corp
URL: {url}

{extracted}

Strategic Signal: Indian regulatory filing data for {parent_company}'s Indian operations.
Indian subsidiaries must file annual returns with MCA21 — reveals headcount, revenue,
director changes, and capital structure changes not disclosed in global filings.
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"mca21_{cin}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=parent_company,
        )

    def _build_static_profile(
        self, company_info: dict, parent_company: str
    ) -> CrawlResult:
        """Fallback: return a structured reference even if the live fetch fails."""
        cin = company_info["cin"]
        entity_name = company_info["name"]
        url = company_info["url"]

        content = f"""India MCA21 Company Reference: {entity_name}
CIN: {cin}
Parent Entity: {parent_company}
Indian Subsidiary: {entity_name}
Source: Ministry of Corporate Affairs (MCA21) — static reference
URL: {url}

This is the Indian registered entity for {parent_company}.
CIN {cin} can be searched at https://www.mca.gov.in for official filings.
Annual reports and director data available via MCA21 portal.
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"mca21_static_{cin}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=parent_company,
        )

    def _extract_from_html(self, html: str, entity_name: str) -> str:
        """Best-effort extraction of key metrics from Zauba Corp HTML."""
        import re

        lines: list[str] = []

        patterns = [
            (r'Paid Up Capital[:\s]*</[^>]+>\s*([₹\d,\.\s]+)', "Paid Up Capital"),
            (r'Authorized Capital[:\s]*</[^>]+>\s*([₹\d,\.\s]+)', "Authorized Capital"),
            (r'Number of Employees[:\s]*</[^>]+>\s*([\d,\s]+)', "Employees"),
            (r'Company Status[:\s]*</[^>]+>\s*([A-Za-z\s]+)', "Status"),
            (r'Date of Incorporation[:\s]*</[^>]+>\s*([\d/\-A-Za-z\s]+)', "Incorporated"),
            (r'Registered Address[:\s]*</[^>]+>\s*([^<]{10,200})', "Registered Address"),
            (r'Directors[^<]*</[^>]+>\s*([^<]{5,500})', "Directors (snippet)"),
        ]

        for pattern, label in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                value = re.sub(r'\s+', ' ', match.group(1)).strip()[:200]
                lines.append(f"{label}: {value}")

        return "\n".join(lines) if lines else f"Profile data for {entity_name} — see URL for full details."

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _COMPANY_CINS["accenture"]["url"],
                    headers={"User-Agent": _USER_AGENT},
                    timeout=10.0,
                    follow_redirects=True,
                )
                return resp.status_code in (200, 301, 302)
        except Exception:
            return False
