"""Source Discovery Tools — finds where a company publishes online.

Tiered approach, cheapest first:
  1. Google News RSS    — free, no key, always-on
  2. RSS autodiscovery  — scan company domain for feed links + common paths
  3. Sitemap crawl      — extract blog/news/press section roots
  4. DuckDuckGo search  — unofficial, no key, finds official domain
  Tier 5 (Tavily) lives in DiscoveryAgent, not here.

All functions are side-effect-free and safe to retry.
All functions swallow errors and return empty results — callers are not
expected to handle exceptions from discovery.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree

import httpx

from observability.logger import get_logger

log = get_logger("source_discovery")

_TIMEOUT = httpx.Timeout(12.0, connect=5.0)

_USER_AGENT = "Mozilla/5.0 (compatible; MarketIntelBot/1.0; +https://example.com)"

_COMMON_RSS_PATHS = [
    "/rss",
    "/rss.xml",
    "/feed",
    "/feed.xml",
    "/atom.xml",
    "/feeds/all.rss",
    "/blog/rss",
    "/blog/feed",
    "/blog/rss.xml",
    "/news/rss",
    "/news/feed",
    "/insights/rss",
    "/newsroom/rss",
    "/press/rss",
    "/publications/rss",
    "/media/rss",
]

_NEWS_SECTION_RE = re.compile(
    r"/(blog|news|insights|press|newsroom|publications|media|updates|articles)/",
    re.IGNORECASE,
)

# Both attribute orderings of <link rel="alternate" type="...rss...">
_RSS_LINK_RE = re.compile(
    r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RSS_HREF_RE = re.compile(
    r'href=["\']([^"\']+)["\'][^>]*type=["\']application/(?:rss|atom)\+xml["\']',
    re.IGNORECASE,
)


def google_news_rss_url(company_name: str) -> str:
    """Build a Google News RSS URL for a company name. No API key required."""
    return (
        f"https://news.google.com/rss/search?q={quote(company_name)}"
        f"&hl=en-US&gl=US&ceid=US:en"
    )


async def autodiscover_rss_feeds(
    domain: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Scan a company domain for RSS / Atom feeds.

    Steps:
      1. Fetch homepage HTML, extract <link rel="alternate" type="application/rss+xml"> tags
      2. HTTP HEAD against a list of common feed paths

    Returns deduplicated absolute feed URLs. Never raises.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )

    feeds: set[str] = set()
    base = domain if domain.startswith("http") else f"https://{domain}"
    base = base.rstrip("/")

    try:
        # Step 1: homepage link tags
        try:
            resp = await client.get(base, timeout=12.0)
            if resp.status_code == 200:
                head_section = resp.text[:60_000]
                for pattern in (_RSS_LINK_RE, _RSS_HREF_RE):
                    for href in pattern.findall(head_section):
                        feeds.add(urljoin(str(resp.url), href))
        except Exception as exc:
            log.info("homepage_fetch_failed", domain=domain, error=str(exc)[:120])

        # Step 2: probe all common paths concurrently (cap at 4s per path)
        async def _probe(path: str) -> str | None:
            try:
                resp = await client.head(base + path, timeout=4.0)
                return str(resp.url) if resp.status_code == 200 else None
            except Exception:
                return None

        hits = await asyncio.gather(*(_probe(p) for p in _COMMON_RSS_PATHS))
        for hit in hits:
            if hit:
                feeds.add(hit)

    finally:
        if own_client:
            await client.aclose()

    result = list(feeds)
    if result:
        log.info("rss_autodiscovery_done", domain=domain, feeds=result)
    return result


async def discover_from_sitemap(
    domain: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Extract news/blog/press section base URLs from a company sitemap.

    Returns section root URLs (e.g. https://company.com/insights/) that
    can be passed to Firecrawl for deep crawl. Never raises.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )

    sections: set[str] = set()
    base = domain if domain.startswith("http") else f"https://{domain}"
    base = base.rstrip("/")

    try:
        # Find sitemap URL via robots.txt first
        candidate_sitemaps = [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
        ]
        try:
            resp = await client.get(f"{base}/robots.txt", timeout=8.0)
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        candidate_sitemaps.insert(0, line.split(":", 1)[1].strip())
                        break
        except Exception:
            pass

        # Parse first working sitemap
        for sitemap_url in candidate_sitemaps:
            try:
                resp = await client.get(sitemap_url, timeout=12.0)
                if resp.status_code != 200:
                    continue
                tree = ElementTree.fromstring(resp.content)
                sm_ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
                for loc_el in tree.iter(f"{sm_ns}loc"):
                    url = (loc_el.text or "").strip()
                    if _NEWS_SECTION_RE.search(url):
                        parsed = urlparse(url)
                        # Use just the first path segment as the section root
                        parts = [p for p in parsed.path.strip("/").split("/") if p]
                        if parts:
                            section_root = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/"
                            sections.add(section_root)
                if sections:
                    break  # stop on first successful sitemap
            except Exception as exc:
                log.info("sitemap_parse_failed", url=sitemap_url, error=str(exc)[:120])

    finally:
        if own_client:
            await client.aclose()

    result = list(sections)
    if result:
        log.info("sitemap_sections_found", domain=domain, sections=result)
    return result


async def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """Text search via DuckDuckGo. No API key required.

    Returns list of {title, url, snippet}. Returns empty list on any error
    (including import failure if duckduckgo-search is not installed).
    """
    try:
        from duckduckgo_search import DDGS  # optional dependency

        results: list[dict] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        log.info("duckduckgo_search_done", query=query[:80], count=len(results))
        return results
    except ImportError:
        log.warning("duckduckgo_unavailable", reason="pip install duckduckgo-search")
        return []
    except Exception as exc:
        log.warning("duckduckgo_search_failed", query=query[:80], error=str(exc)[:120])
        return []
