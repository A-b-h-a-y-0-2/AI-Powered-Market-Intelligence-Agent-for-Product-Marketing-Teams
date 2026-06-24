"""GitHub organization activity monitor.

Tracks public repository activity, releases, and new repos for consulting firm
open-source arms. Tech-forward consultancies signal product direction through
what they open-source before launching commercially.

Signals:
  - New repo creation → emerging capability area
  - Release published → production-ready tool being promoted to clients
  - Sudden burst of activity → team buildout / product push

Known GitHub orgs:
  Accenture     → AccentureOpenSource   (prolific: AI, Intelligent Automation)
  BCG X         → BCG-X-official        (data science, climate)
  Deloitte      → deloitteai            (limited public activity)

API: https://api.github.com/
Auth: Free token at https://github.com/settings/tokens (5000 req/hour vs 60 unauth)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from observability.logger import get_logger
from schemas.state import CrawlResult

log = get_logger("github_monitor_tool")

_BASE_URL = "https://api.github.com"

# Known GitHub orgs per company (primary signal sources)
_COMPANY_ORGS: dict[str, list[str]] = {
    "accenture": ["Accenture"],
    "accenture strategy": ["Accenture"],
    "boston consulting group": ["BCG-X"],
    "bcg": ["BCG-X"],
    "deloitte": ["deloitteai"],
    "kpmg": ["KPMG"],
    "oliver wyman": [],
    "mckinsey": [],
    "bain": [],
}


class GitHubMonitorTool:
    """Monitors GitHub organizations for new repos and releases from target firms."""

    def __init__(
        self,
        github_token: str | None = None,
        timeout_seconds: float = 20.0,
        max_repos: int = 30,
        max_releases_per_repo: int = 3,
    ) -> None:
        self._token = github_token
        self._timeout = timeout_seconds
        self._max_repos = max_repos
        self._max_releases = max_releases_per_repo

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get_orgs(self, company_name: str, input_orgs: list[str] | None = None) -> list[str]:
        if input_orgs:
            return input_orgs
        name_lower = company_name.lower()
        for key, orgs in _COMPANY_ORGS.items():
            if key in name_lower:
                return orgs
        return []

    async def monitor_org(
        self,
        company_name: str,
        days: int = 30,
        orgs: list[str] | None = None,
    ) -> list[CrawlResult]:
        target_orgs = self._get_orgs(company_name, orgs)
        if not target_orgs:
            log.info(
                "github_no_orgs",
                company=company_name,
                message="No GitHub orgs configured for this company",
            )
            return []

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        all_results: list[CrawlResult] = []

        for org in target_orgs:
            repos = await self._list_org_repos(org)
            for repo in repos:
                pushed_at = repo.get("pushed_at", "")
                try:
                    pushed_dt = datetime.fromisoformat(
                        pushed_at.replace("Z", "+00:00")
                    )
                    if pushed_dt < cutoff:
                        continue
                except (ValueError, AttributeError):
                    pass

                # New repo created recently
                created_at = repo.get("created_at", "")
                try:
                    created_dt = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
                    if created_dt >= cutoff:
                        result = self._new_repo_to_crawl_result(repo, company_name, org)
                        if result:
                            all_results.append(result)
                except (ValueError, AttributeError):
                    pass

                # Recent releases
                releases = await self._list_repo_releases(org, repo["name"])
                for release in releases:
                    pub_at = release.get("published_at", "")
                    try:
                        pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                        if pub_dt < cutoff:
                            continue
                    except (ValueError, AttributeError):
                        continue
                    result = self._release_to_crawl_result(
                        release, repo, company_name, org
                    )
                    if result:
                        all_results.append(result)

        log.info(
            "github_monitor_complete",
            company=company_name,
            orgs=target_orgs,
            results=len(all_results),
        )
        return all_results

    async def _list_org_repos(self, org: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/orgs/{org}/repos",
                    params={
                        "sort": "pushed",
                        "direction": "desc",
                        "per_page": self._max_repos,
                        "type": "public",
                    },
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            log.error("github_list_repos_failed", org=org, error=str(exc))
            return []

    async def _list_repo_releases(
        self, org: str, repo_name: str
    ) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/repos/{org}/{repo_name}/releases",
                    params={"per_page": self._max_releases},
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            log.warning(
                "github_list_releases_failed",
                org=org,
                repo=repo_name,
                error=str(exc),
            )
            return []

    def _new_repo_to_crawl_result(
        self, repo: dict, company: str, org: str
    ) -> CrawlResult | None:
        full_name = repo.get("full_name", "")
        if not full_name:
            return None

        url = repo.get("html_url", f"https://github.com/{full_name}")
        name = repo.get("name", "")
        description = repo.get("description", "") or ""
        language = repo.get("language", "") or ""
        stars = repo.get("stargazers_count", 0)
        topics = repo.get("topics", [])
        created_at = repo.get("created_at", "")

        content = f"""New GitHub Repository: {full_name}
Organization: {org} ({company})
Created: {created_at}
Language: {language}
Stars: {stars}
Topics: {', '.join(topics)}
Description: {description}
Repository URL: {url}
Source: GitHub (public open-source activity)

Strategic Signal: {company} published new open-source repository "{name}" on GitHub.
Description: {description}
Topics signal: {', '.join(topics) or 'none tagged'}
New repositories reveal emerging technology capabilities before they appear in product announcements.
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"gh_repo_{full_name}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company,
        )

    def _release_to_crawl_result(
        self, release: dict, repo: dict, company: str, org: str
    ) -> CrawlResult | None:
        release_id = str(release.get("id", ""))
        if not release_id:
            return None

        tag = release.get("tag_name", "")
        release_name = release.get("name", tag)
        published_at = release.get("published_at", "")
        body = (release.get("body", "") or "")[:600]
        prerelease = release.get("prerelease", False)
        url = release.get("html_url", "")

        repo_name = repo.get("name", "")
        repo_description = repo.get("description", "") or ""

        content = f"""GitHub Release: {release_name} ({tag})
Repository: {org}/{repo_name} ({company})
Published: {published_at}
Pre-release: {prerelease}
Repository Description: {repo_description}
Release Notes: {body}
URL: {url}
Source: GitHub Releases

Strategic Signal: {company} published release "{release_name}" for open-source project "{repo_name}".
{repo_description}
This signals active development and client-facing tools being maintained.
Release notes: {body[:300]}
"""

        return CrawlResult(
            url=url,
            content=content,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(f"gh_release_{release_id}".encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            company=company,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/orgs/AccentureOpenSource/repos",
                    params={"per_page": 1},
                    headers=self._headers(),
                    timeout=10.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
