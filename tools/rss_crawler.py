"""RSS/Atom feed crawler for 30-minute polling of competitor news feeds.

Parses standard RSS 2.0 and Atom 1.0 feeds. Returns structured entries
with normalized timestamps (ISO 8601). Handles missing/malformed dates
gracefully by falling back to the crawl timestamp.

Called by APScheduler every 30 minutes; runs concurrently for all feeds.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from observability.logger import get_logger
from tools.errors import CrawlError, ErrorCode

log = get_logger("rss_crawler")

# Minimal XML parsing without lxml dependency — feedparser handles the heavy lifting
try:
    import feedparser

    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False


class RSSEntry:
    """A single parsed RSS/Atom entry."""

    __slots__ = (
        "title",
        "url",
        "summary",
        "content",
        "published_at",
        "author",
        "tags",
        "entry_hash",
    )

    def __init__(
        self,
        title: str,
        url: str,
        summary: str,
        content: str,
        published_at: str,
        author: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.title = title
        self.url = url
        self.summary = summary
        self.content = content
        self.published_at = published_at
        self.author = author
        self.tags = tags or []
        # Stable hash for deduplication (url + title)
        self.entry_hash = hashlib.sha256(f"{url}{title}".encode()).hexdigest()[:16]

    def __repr__(self) -> str:
        return f"RSSEntry(title={self.title!r}, url={self.url!r}, published={self.published_at!r})"


class RSSFeedResult:
    """Result of parsing one RSS feed."""

    __slots__ = ("feed_url", "feed_title", "entries", "entries_count", "crawl_timestamp")

    def __init__(
        self,
        feed_url: str,
        feed_title: str,
        entries: list[RSSEntry],
        crawl_timestamp: str,
    ) -> None:
        self.feed_url = feed_url
        self.feed_title = feed_title
        self.entries = entries
        self.entries_count = len(entries)
        self.crawl_timestamp = crawl_timestamp


class RSSCrawler:
    """Async RSS/Atom feed crawler.

    Uses feedparser for format-agnostic parsing. Falls back to direct HTTP
    parsing if feedparser is not available (unlikely — it ships as a dependency).

    Dependency-injected HTTP client. No global state.
    """

    _DEFAULT_TIMEOUT = 30.0
    _MAX_ENTRIES_PER_FEED = 50
    _STRIP_HTML_RE = re.compile(r"<[^>]+>")

    def __init__(self, timeout_seconds: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            headers={
                "User-Agent": "MarketIntelligenceBot/1.0 (competitive intelligence crawler)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            },
            follow_redirects=True,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "RSSCrawler":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()

    @retry(
        retry=retry_if_exception_type(CrawlError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def fetch_feed(
        self,
        feed_url: str,
        company: str,
        max_entries: int = _MAX_ENTRIES_PER_FEED,
        since_timestamp: str | None = None,
    ) -> RSSFeedResult:
        """Fetch and parse a single RSS or Atom feed.

        Args:
            feed_url: The URL of the RSS/Atom feed.
            company: Company name (for logging).
            max_entries: Maximum number of entries to return.
            since_timestamp: ISO 8601 datetime; skip entries older than this.

        Returns:
            RSSFeedResult with parsed entries.

        Raises:
            CrawlError: On HTTP failure or unparseable content.
        """
        if not self._client:
            raise CrawlError(
                code=ErrorCode.CRAWL_FAILED,
                message="RSSCrawler not connected. Call connect() first.",
                source_url=feed_url,
            )

        crawl_timestamp = datetime.now(tz=timezone.utc).isoformat()
        log.info(
            "rss_fetch_started",
            agent="rss_crawler",
            action="fetch_feed",
            source=feed_url,
            company=company,
        )

        # Fetch raw content
        try:
            response = await self._client.get(feed_url)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise CrawlError(
                code=ErrorCode.CRAWL_TIMEOUT,
                message=f"RSS feed timed out: {feed_url}",
                source_url=feed_url,
                cause=exc,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise CrawlError(
                code=ErrorCode.CRAWL_FAILED,
                message=f"RSS fetch HTTP {exc.response.status_code}: {feed_url}",
                source_url=feed_url,
                cause=exc,
            ) from exc
        except httpx.RequestError as exc:
            raise CrawlError(
                code=ErrorCode.CRAWL_FAILED,
                message=f"RSS request error for {feed_url}: {exc}",
                source_url=feed_url,
                cause=exc,
            ) from exc

        # Parse feed content
        raw_content = response.text
        try:
            entries, feed_title = self._parse_feed(
                raw_content=raw_content,
                feed_url=feed_url,
                crawl_timestamp=crawl_timestamp,
                max_entries=max_entries,
                since_timestamp=since_timestamp,
            )
        except Exception as exc:
            raise CrawlError(
                code=ErrorCode.CRAWL_PARSE_ERROR,
                message=f"Failed to parse RSS feed {feed_url}: {exc}",
                source_url=feed_url,
                cause=exc,
            ) from exc

        result = RSSFeedResult(
            feed_url=feed_url,
            feed_title=feed_title,
            entries=entries,
            crawl_timestamp=crawl_timestamp,
        )

        log.info(
            "rss_fetch_completed",
            agent="rss_crawler",
            action="fetch_feed",
            source=feed_url,
            company=company,
            entries=len(entries),
            status="ok",
        )
        return result

    def _parse_feed(
        self,
        raw_content: str,
        feed_url: str,
        crawl_timestamp: str,
        max_entries: int,
        since_timestamp: str | None,
    ) -> tuple[list[RSSEntry], str]:
        """Parse RSS/Atom content into structured entries."""
        if not _HAS_FEEDPARSER:
            raise CrawlError(
                code=ErrorCode.CRAWL_PARSE_ERROR,
                message="feedparser not installed. Run: pip install feedparser",
                source_url=feed_url,
            )

        parsed = feedparser.parse(raw_content)

        if parsed.bozo and not parsed.entries:
            bozo_exc = getattr(parsed, "bozo_exception", None)
            raise CrawlError(
                code=ErrorCode.CRAWL_PARSE_ERROR,
                message=f"feedparser could not parse feed: {bozo_exc}",
                source_url=feed_url,
            )

        feed_title = (
            getattr(parsed.feed, "title", None)
            or getattr(parsed.feed, "subtitle", None)
            or feed_url
        )

        # Parse since_timestamp for filtering
        since_dt: datetime | None = None
        if since_timestamp:
            try:
                since_dt = datetime.fromisoformat(since_timestamp.replace("Z", "+00:00"))
            except ValueError:
                since_dt = None

        entries: list[RSSEntry] = []
        for raw_entry in parsed.entries[:max_entries]:
            entry = self._parse_entry(raw_entry, crawl_timestamp)
            if entry is None:
                continue

            # Filter by since_timestamp if provided
            if since_dt:
                try:
                    entry_dt = datetime.fromisoformat(entry.published_at.replace("Z", "+00:00"))
                    if entry_dt <= since_dt:
                        continue
                except ValueError:
                    pass  # Include if we can't parse the date

            entries.append(entry)

        return entries, feed_title

    def _parse_entry(self, raw_entry: Any, crawl_timestamp: str) -> RSSEntry | None:
        """Parse a single feedparser entry object into an RSSEntry."""
        # URL is required
        url = getattr(raw_entry, "link", None)
        if not url:
            return None

        title = self._clean_text(getattr(raw_entry, "title", ""))

        # Extract content: prefer full content over summary
        content = ""
        if hasattr(raw_entry, "content") and raw_entry.content:
            content = self._clean_html(raw_entry.content[0].get("value", ""))
        elif hasattr(raw_entry, "summary"):
            content = self._clean_html(raw_entry.summary or "")

        summary = self._clean_html(getattr(raw_entry, "summary", "")) or content[:500]

        # Parse publish date
        published_at = crawl_timestamp
        if hasattr(raw_entry, "published_parsed") and raw_entry.published_parsed:
            try:
                dt = datetime(*raw_entry.published_parsed[:6], tzinfo=timezone.utc)
                published_at = dt.isoformat()
            except (ValueError, TypeError):
                pass
        elif hasattr(raw_entry, "published") and raw_entry.published:
            try:
                dt = parsedate_to_datetime(raw_entry.published)
                published_at = dt.isoformat()
            except Exception:
                pass
        elif hasattr(raw_entry, "updated_parsed") and raw_entry.updated_parsed:
            try:
                dt = datetime(*raw_entry.updated_parsed[:6], tzinfo=timezone.utc)
                published_at = dt.isoformat()
            except (ValueError, TypeError):
                pass

        author = None
        if hasattr(raw_entry, "author"):
            author = str(raw_entry.author)[:100]

        tags: list[str] = []
        if hasattr(raw_entry, "tags"):
            tags = [t.get("term", "") for t in raw_entry.tags if t.get("term")]

        return RSSEntry(
            title=title,
            url=url,
            summary=summary[:1000],
            content=content[:10000],
            published_at=published_at,
            author=author,
            tags=tags,
        )

    def _clean_html(self, text: str) -> str:
        """Strip HTML tags and decode entities."""
        if not text:
            return ""
        text = self._STRIP_HTML_RE.sub(" ", text)
        text = html.unescape(text)
        # Collapse whitespace
        text = " ".join(text.split())
        return text

    def _clean_text(self, text: str) -> str:
        """Decode HTML entities in plain text fields."""
        if not text:
            return ""
        return html.unescape(text).strip()

    async def fetch_multiple(
        self,
        feeds: list[dict[str, str]],
        max_concurrent: int = 5,
        since_timestamp: str | None = None,
    ) -> list[RSSFeedResult]:
        """Fetch multiple feeds concurrently with bounded parallelism.

        Args:
            feeds: List of dicts with keys "url" and "company".
            max_concurrent: Maximum concurrent HTTP connections.
            since_timestamp: Skip entries older than this ISO 8601 datetime.

        Returns:
            List of RSSFeedResult (failures are logged and skipped, not raised).
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _fetch_one(feed_def: dict[str, str]) -> RSSFeedResult | None:
            async with semaphore:
                try:
                    return await self.fetch_feed(
                        feed_url=feed_def["url"],
                        company=feed_def["company"],
                        since_timestamp=since_timestamp,
                    )
                except CrawlError as exc:
                    log.error(
                        "rss_fetch_failed",
                        agent="rss_crawler",
                        source=feed_def["url"],
                        company=feed_def["company"],
                        error_code=exc.code,
                        error=str(exc),
                        status="failed",
                    )
                    return None

        import asyncio

        results = await asyncio.gather(*[_fetch_one(f) for f in feeds])
        return [r for r in results if r is not None]

    async def health_check(self) -> dict[str, str]:
        """Verify the crawler can make HTTP requests."""
        try:
            # Use a known stable public RSS feed for health check
            result = await self.fetch_feed(
                feed_url="https://feeds.feedburner.com/TechCrunch/",
                company="health_check",
                max_entries=1,
            )
            return {"status": "ok", "entries": str(result.entries_count)}
        except CrawlError as exc:
            return {"status": "error", "error_code": exc.code, "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "error_code": ErrorCode.CRAWL_FAILED, "message": str(exc)}
