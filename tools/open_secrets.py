"""OpenSecrets lobbying intelligence tool.

Tracks federal lobbying spend by consulting and professional services firms.
High lobbying spend → regulatory risk awareness, anticipating regulatory change.
Sudden spend increase → major policy threat or opportunity being defended.
Lobbying issues list → reveals what they fear (or want) legislators to do.

API: https://www.opensecrets.org/api/
Auth: Free key at https://www.opensecrets.org/api/admin/
Rate limit: 200 req/day on free plan.

Coverage: US-only. Best signal for Accenture Federal, Deloitte, KPMG (all have
significant government practice revenues that depend on regulatory environment).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("open_secrets_tool")

_BASE_URL = "https://www.opensecrets.org/api/"

# OpenSecrets org IDs — found via getOrgs API or OpenSecrets website
# These are the primary entities for tracked firms with major lobbying activity
_ORG_IDS: dict[str, str] = {
    "accenture": "D000021937",
    "accenture strategy": "D000021937",
    "deloitte": "D000023946",
    "kpmg": "D000027118",
    "mckinsey": "D000066654",
    "boston consulting group": "D000048283",
    "bcg": "D000048283",
}

_LOBBYING_CYCLES = ["2024", "2025", "2026"]  # Most recent cycles


class OpenSecretsTool:
    """Pulls lobbying spend and issue data from OpenSecrets."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds

    def _get_org_id(self, company_name: str) -> str | None:
        name_lower = company_name.lower()
        for key, org_id in _ORG_IDS.items():
            if key in name_lower:
                return org_id
        return None

    async def search_lobbying(
        self,
        company_name: str,
        days: int = 365,
    ) -> list[CrawlResult]:
        if not self._api_key:
            log.warning(
                "open_secrets_skipped",
                reason="OPEN_SECRETS_API_KEY not configured",
                company=company_name,
            )
            return []

        org_id = self._get_org_id(company_name)
        if not org_id:
            # Try dynamic search if no pre-mapped ID
            org_id = await self._find_org_id(company_name)
        if not org_id:
            log.info(
                "open_secrets_no_org",
                company=company_name,
                message="No OpenSecrets org ID found",
            )
            return []

        results: list[CrawlResult] = []
        for cycle in _LOBBYING_CYCLES:
            summary = await self._get_org_summary(org_id, company_name, cycle)
            if summary:
                results.append(summary)

        issues = await self._get_lobbying_issues(org_id, company_name)
        results.extend(issues)

        log.info(
            "open_secrets_complete",
            company=company_name,
            org_id=org_id,
            results=len(results),
        )
        return results

    async def _find_org_id(self, company_name: str) -> str | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _BASE_URL,
                    params={
                        "method": "getOrgs",
                        "org": company_name,
                        "apikey": self._api_key,
                        "output": "json",
                    },
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                orgs = data.get("response", {}).get("organization", [])
                if isinstance(orgs, dict):
                    orgs = [orgs]
                if orgs:
                    attrs = orgs[0].get("@attributes", orgs[0])
                    return attrs.get("orgid", "")
        except Exception as exc:
            log.error("open_secrets_org_search_failed", company=company_name, error=str(exc))
        return None

    async def _get_org_summary(
        self, org_id: str, company_name: str, cycle: str
    ) -> CrawlResult | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _BASE_URL,
                    params={
                        "method": "orgSummary",
                        "id": org_id,
                        "cycle": cycle,
                        "apikey": self._api_key,
                        "output": "json",
                    },
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error("open_secrets_summary_failed", org_id=org_id, error=str(exc))
            return None

        org_data = data.get("response", {}).get("organization", {})
        attrs = org_data.get("@attributes", org_data)

        if not attrs:
            return None

        total_spent = attrs.get("lobbying", "0")
        pacs = attrs.get("pacs", "0")
        org_name = attrs.get("orgname", company_name)

        url = f"https://www.opensecrets.org/orgs/summary?id={org_id}&cycle={cycle}"
        content = f"""Federal Lobbying Disclosure: {org_name} ({cycle})
Organization: {org_name}
Cycle: {cycle}
Total Lobbying Spend: ${float(total_spent or 0):,.0f}
PAC Contributions: ${float(pacs or 0):,.0f}
OpenSecrets ID: {org_id}
Source: OpenSecrets.org (Federal lobbying disclosures)

Strategic Signal: {company_name} spent ${float(total_spent or 0):,.0f} on federal lobbying in {cycle}.
High lobbying spend indicates active regulatory risk management or policy shaping.
PAC activity: ${float(pacs or 0):,.0f}.
Full profile: {url}
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"os_summary_{org_id}_{cycle}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company_name,
        )

    async def _get_lobbying_issues(
        self, org_id: str, company_name: str
    ) -> list[CrawlResult]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _BASE_URL,
                    params={
                        "method": "lobbyIssues",
                        "id": org_id,
                        "cycle": "2026",
                        "apikey": self._api_key,
                        "output": "json",
                    },
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error("open_secrets_issues_failed", org_id=org_id, error=str(exc))
            return []

        issues_data = data.get("response", {}).get("lobbyIssues", {})
        issues = issues_data.get("issue", [])
        if isinstance(issues, dict):
            issues = [issues]

        if not issues:
            return []

        issues_text = "\n".join(
            f"  - {i.get('@attributes', i).get('issue_code', 'Unknown')}: "
            f"${float(i.get('@attributes', i).get('total', 0)):,.0f}"
            for i in issues[:10]
        )

        url = f"https://www.opensecrets.org/orgs/lobbying?id={org_id}"
        content = f"""Federal Lobbying Issues: {company_name} (2026)
Organization: {company_name}
OpenSecrets ID: {org_id}
Source: OpenSecrets.org / FARA (Federal lobbying disclosures)

Top Lobbying Issues by Spend:
{issues_text}

Strategic Signal: The issues {company_name} is lobbying on reveal regulatory risks
they're managing and policy changes they're anticipating or shaping.
Full breakdown: {url}
"""

        return [CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"os_issues_{org_id}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company_name,
        )]

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _BASE_URL,
                    params={
                        "method": "orgSummary",
                        "id": "D000021937",
                        "cycle": "2024",
                        "apikey": self._api_key,
                        "output": "json",
                    },
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
