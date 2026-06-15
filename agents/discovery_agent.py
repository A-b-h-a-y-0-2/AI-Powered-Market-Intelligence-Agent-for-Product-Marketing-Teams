"""Discovery Agent — autonomously finds where a company publishes online.

Single responsibility: given a company name (and optional domain hint),
discover all relevant RSS feeds, blog sections, and press rooms — without
requiring manually maintained YAML config.

Input:  company name (str) + optional domain hint (str)
Output: DiscoveryOutput — typed list of DiscoveredSource objects

Discovery tiers (cheapest first):
  1. Google News RSS     — free, always-on, no API key
  2. RSS autodiscovery   — scan company domain for <link rel="alternate"> + common paths
  3. Sitemap crawl       — extract blog/news section roots for Firecrawl
  4. DuckDuckGo search   — find official domain when not provided
  5. Tavily search       — paid, only when tiers 1–3 yield < 2 native sources

Output cached in Redis for 7 days per company. Force-refresh via invalidate_cache().
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field

from observability.logger import get_logger
from storage.cache import CacheStore
from tools.source_discovery import (
    autodiscover_rss_feeds,
    discover_from_sitemap,
    google_news_rss_url,
    search_duckduckgo,
)

log = get_logger("discovery_agent")

_DISCOVERY_CACHE_TTL = 7 * 24 * 3600  # 7 days
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MarketIntelBot/1.0)"}


# ── Schemas ───────────────────────────────────────────────────────────────────

class DiscoveredSource(BaseModel):
    url: str
    source_type: str = Field(description="'rss' or 'firecrawl'")
    frequency: str = Field(default="30min")
    confidence: float = Field(ge=0.0, le=1.0, description="0–1 source quality estimate")
    discovered_via: str = Field(
        description="'google_news_rss' | 'rss_autodiscovery' | 'sitemap' | 'duckduckgo' | 'tavily'"
    )


class DiscoveryOutput(BaseModel):
    company_name: str
    domain: Optional[str] = None
    sources: list[DiscoveredSource]
    discovered_at: str
    from_cache: bool = False


# ── Agent ─────────────────────────────────────────────────────────────────────

class DiscoveryAgent:
    """Finds content sources for a company without any predefined YAML config.

    Usage:
        agent = DiscoveryAgent(cache=cache_store, tavily_search=tavily)
        result = await agent.discover("Boston Consulting Group", domain="bcg.com")
        # result.sources: list of DiscoveredSource ready to feed ResearchAgent
    """

    name = "discovery_agent"
    description = (
        "Autonomously discovers RSS feeds, blog sections, and news sources for any "
        "company. No YAML config needed — just a company name and optional domain."
    )

    def __init__(
        self,
        cache: CacheStore,
        tavily_search=None,
    ) -> None:
        self._cache = cache
        self._tavily = tavily_search

    # ── Public API ────────────────────────────────────────────────────────────

    async def discover(
        self,
        company_name: str,
        domain: Optional[str] = None,
        force_refresh: bool = False,
    ) -> DiscoveryOutput:
        """Discover all sources for a company. Cached 7 days per company."""
        cache_key = f"discovery:{_stable_key(company_name)}"

        if not force_refresh:
            try:
                cached_raw = await self._cache.get(cache_key)
                if cached_raw:
                    result = DiscoveryOutput.model_validate_json(cached_raw)
                    result.from_cache = True
                    log.info("discovery_cache_hit", company=company_name)
                    return result
            except Exception:
                pass  # treat cache miss on error

        log.info("discovery_started", company=company_name, domain=domain)
        sources: list[DiscoveredSource] = []

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers=_HTTP_HEADERS,
        ) as client:

            # ── Tier 1a: Google News RSS ──────────────────────────────────────
            # Always included. Captures news aggregated from all over the web.
            sources.append(DiscoveredSource(
                url=google_news_rss_url(company_name),
                source_type="rss",
                frequency="30min",
                confidence=0.85,
                discovered_via="google_news_rss",
            ))

            # ── Tier 4 first if no domain: DuckDuckGo → find domain ──────────
            resolved_domain = domain
            if not resolved_domain:
                resolved_domain = await self._find_domain(company_name)

            # ── Tier 1b: RSS autodiscovery from company domain ────────────────
            if resolved_domain:
                rss_feeds = await autodiscover_rss_feeds(resolved_domain, client)
                for feed_url in rss_feeds:
                    sources.append(DiscoveredSource(
                        url=feed_url,
                        source_type="rss",
                        frequency="30min",
                        confidence=0.95,
                        discovered_via="rss_autodiscovery",
                    ))

            # ── Tier 3: Sitemap → Firecrawl section roots ────────────────────
            if resolved_domain:
                section_roots = await discover_from_sitemap(resolved_domain, client)
                for section_url in section_roots:
                    sources.append(DiscoveredSource(
                        url=section_url,
                        source_type="firecrawl",
                        frequency="daily",
                        confidence=0.80,
                        discovered_via="sitemap",
                    ))

            # ── Tier 5: Tavily — only when native sources are thin ────────────
            native = [s for s in sources if s.discovered_via != "google_news_rss"]
            if len(native) < 2 and self._tavily:
                tavily_sources = await self._discover_via_tavily(company_name)
                sources.extend(tavily_sources)

        # Deduplicate by URL, preserve insertion order
        seen: set[str] = set()
        unique: list[DiscoveredSource] = []
        for src in sources:
            if src.url not in seen:
                seen.add(src.url)
                unique.append(src)

        result = DiscoveryOutput(
            company_name=company_name,
            domain=resolved_domain,
            sources=unique,
            discovered_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        try:
            await self._cache.set(
                cache_key, result.model_dump_json(), ttl_seconds=_DISCOVERY_CACHE_TTL
            )
        except Exception as exc:
            log.warning("discovery_cache_write_failed", company=company_name, error=str(exc)[:120])

        log.info(
            "discovery_completed",
            agent=self.name,
            company=company_name,
            domain=resolved_domain,
            total_sources=len(unique),
            rss=sum(1 for s in unique if s.source_type == "rss"),
            firecrawl=sum(1 for s in unique if s.source_type == "firecrawl"),
            from_cache=False,
        )
        return result

    async def invalidate_cache(self, company_name: str) -> None:
        """Force re-discovery on next call."""
        try:
            await self._cache.delete(f"discovery:{_stable_key(company_name)}")
            log.info("discovery_cache_invalidated", company=company_name)
        except Exception:
            pass

    async def health_check(self) -> dict:
        return {"agent": self.name, "status": "ok"}

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _find_domain(self, company_name: str) -> str | None:
        """DuckDuckGo: find the company's primary domain from its name."""
        results = await search_duckduckgo(
            f"{company_name} official website", max_results=5
        )
        # Prefer a result whose domain contains a meaningful part of the company name
        name_tokens = set(
            t.lower()
            for t in company_name.replace("&", "").split()
            if len(t) > 2 and t.lower() not in {"the", "and", "inc", "llc", "ltd"}
        )
        for r in results:
            url = r.get("url", "")
            if not url:
                continue
            netloc = urlparse(url).netloc.lower().lstrip("www.")
            if any(tok in netloc for tok in name_tokens):
                log.info("domain_discovered", company=company_name, domain=netloc, via="duckduckgo")
                return netloc
        # Fallback: first result regardless of name match
        if results and results[0].get("url"):
            netloc = urlparse(results[0]["url"]).netloc.lstrip("www.")
            log.info("domain_discovered_fallback", company=company_name, domain=netloc)
            return netloc
        return None

    async def _discover_via_tavily(
        self, company_name: str
    ) -> list[DiscoveredSource]:
        """Tier 5: Tavily search for official content sources.

        Strategy:
        - If Tavily returns a URL on the company's own domain → extract the
          section root (e.g. /blog/, /insights/) as a firecrawl source.
        - Third-party news URLs are NOT stored as firecrawl targets (they go
          stale immediately). Instead we add a targeted Tavily query source so
          the same topic is searched fresh every day.
        """
        try:
            query = f"{company_name} official blog press releases newsroom insights"
            results = await self._tavily.search(query=query, max_results=8, days=365)
            sources: list[DiscoveredSource] = []
            seen_domains: set[str] = set()
            company_domain = await self._find_domain(company_name)

            for r in results:
                url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None) or ""
                if not url:
                    continue

                parsed = urlparse(url)
                netloc = parsed.netloc.lower().lstrip("www.")

                # Company's own domain → extract section root for firecrawl
                if company_domain and netloc.endswith(company_domain):
                    parts = [p for p in parsed.path.strip("/").split("/") if p]
                    if parts:
                        section_root = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/"
                        if section_root not in seen_domains:
                            seen_domains.add(section_root)
                            sources.append(DiscoveredSource(
                                url=section_root,
                                source_type="firecrawl",
                                frequency="daily",
                                confidence=0.80,
                                discovered_via="tavily",
                            ))
                else:
                    # Third-party site — add as a targeted Tavily query, not a
                    # firecrawl URL (individual articles go stale immediately)
                    if netloc not in seen_domains:
                        seen_domains.add(netloc)
                        title = getattr(r, "title", None) or (r.get("title") if isinstance(r, dict) else None) or ""
                        # Extract topic keywords from the article title
                        topic = _extract_topic(title, company_name)
                        if topic:
                            sources.append(DiscoveredSource(
                                url=f"tavily://query?q={quote(topic)}",
                                source_type="tavily",
                                frequency="daily",
                                confidence=0.65,
                                discovered_via="tavily",
                            ))

            return sources
        except Exception as exc:
            log.warning("tavily_discovery_failed", company=company_name, error=str(exc)[:120])
            return []


def _stable_key(company_name: str) -> str:
    """MD5 of normalised company name — stable, compact Redis key."""
    return hashlib.md5(company_name.lower().strip().encode()).hexdigest()


def _extract_topic(title: str, company_name: str) -> str:
    """Build a search query from an article title by stripping noise words.

    Returns a non-empty query string, or empty string if nothing useful found.
    """
    if not title:
        return ""
    _NOISE = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "its", "it",
        "this", "that", "as", "says", "said", "new", "more", "latest",
    }
    words = [w for w in title.split() if w.lower() not in _NOISE and len(w) > 2]
    # Prepend company name so the query stays targeted
    topic = f"{company_name} {' '.join(words[:8])}".strip()
    return topic if len(topic) > len(company_name) + 3 else ""
