"""Firecrawl-based web crawler with ETag checking, content hashing, and Redis caching.

Pipeline per URL:
  1. HTTP HEAD → check ETag / Last-Modified (zero-cost)
  2. If ETag unchanged → return CONTENT_UNCHANGED
  3. Fetch content via Firecrawl (markdown output)
  4. Hash meaningful content sections (excludes nav/footer/timestamps)
  5. Compare hash against Redis cache
  6. If hash unchanged → update ETag in cache, return CONTENT_UNCHANGED
  7. Update Redis with new hash and ETag
  8. Return CrawlResult with content

Circuit breaker: if a source fails N consecutive times, it is marked OPEN and
subsequent calls raise CircuitOpenError immediately until the recovery probe succeeds.

All errors carry named codes. Every HTTP call has an explicit timeout.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import httpx
from firecrawl import FirecrawlApp
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from observability.logger import get_logger
from schemas.state import CrawlResult
from storage.cache import CacheStore
from tools.errors import (
    CrawlError,
    CircuitOpenError,
    ErrorCode,
)

log = get_logger("crawler")

# Patterns to strip from content before hashing (nav, ads, timestamps, etc.)
_NOISE_PATTERNS = [
    re.compile(r"!\[.*?\]\(.*?\)"),        # markdown images
    re.compile(r"\[.*?\]\(.*?\)"),          # markdown links (keep text)
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),  # ISO timestamps
    re.compile(r"Copyright ©.*", re.IGNORECASE),
    re.compile(r"Privacy Policy.*", re.IGNORECASE),
]

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _hash_content(markdown: str) -> str:
    """Return SHA-256 hash of meaningful content — strips noise before hashing."""
    text = markdown
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub("", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CircuitBreaker:
    """Per-source circuit breaker backed by Redis.

    States: CLOSED (normal) → OPEN (stop sending) → HALF_OPEN (probe)
    """

    def __init__(
        self,
        cache: CacheStore,
        failure_threshold: int = 5,
        recovery_delay_seconds: int = 3600,
    ) -> None:
        self._cache = cache
        self._failure_threshold = failure_threshold
        self._recovery_delay = recovery_delay_seconds

    async def check(self, source_url: str) -> None:
        """Raise CircuitOpenError if the circuit is OPEN for this source."""
        state = await self._cache.get_circuit_state(source_url)
        if state["state"] == "OPEN":
            raise CircuitOpenError(source_url)

    async def record_success(self, source_url: str) -> None:
        await self._cache.reset_failure_count(source_url)
        await self._cache.set_circuit_state(
            source_url, {"state": "CLOSED", "failure_count": 0, "opened_at": None}
        )

    async def record_failure(self, source_url: str, error_code: str) -> None:
        count = await self._cache.increment_failure_count(source_url)
        log.warning(
            "crawl_failure_recorded",
            source=source_url,
            failure_count=count,
            error_code=error_code,
        )
        if count >= self._failure_threshold:
            opened_at = datetime.now(tz=timezone.utc).isoformat()
            await self._cache.set_circuit_state(
                source_url,
                {"state": "OPEN", "failure_count": count, "opened_at": opened_at},
                ttl_seconds=self._recovery_delay,
            )
            log.error(
                "circuit_opened",
                error_code=ErrorCode.CIRCUIT_OPEN,
                source=source_url,
                failure_count=count,
                opened_at=opened_at,
            )


class Crawler:
    """Idempotent web crawler with change detection and circuit breaking.

    Inject dependencies; do not instantiate clients internally.
    """

    def __init__(
        self,
        firecrawl_api_key: str,
        cache: CacheStore,
        request_timeout_seconds: int = 30,
        firecrawl_timeout_seconds: int = 60,
        max_retries: int = 3,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._app = FirecrawlApp(api_key=firecrawl_api_key)
        self._cache = cache
        self._request_timeout = request_timeout_seconds
        self._firecrawl_timeout = firecrawl_timeout_seconds
        self._max_retries = max_retries
        self._circuit_breaker = circuit_breaker

    async def crawl(self, url: str) -> CrawlResult:
        """Crawl a URL, returning a CrawlResult.

        Returns is_changed=False and empty content if content is unchanged.
        Raises CrawlError with a named code on non-recoverable failures.
        Raises CircuitOpenError if the circuit for this source is open.
        """
        if self._circuit_breaker:
            await self._circuit_breaker.check(url)

        crawl_timestamp = datetime.now(tz=timezone.utc).isoformat()
        date_bucket = crawl_timestamp[:10]  # YYYY-MM-DD

        # Step 1: ETag check (cheap HTTP HEAD)
        cached_etag = await self._cache.get_etag(url)
        head_etag, head_last_modified, status_code = await self._http_head(url)

        if head_etag and cached_etag and head_etag == cached_etag:
            log.info(
                "content_unchanged_etag",
                source=url,
                status="skip",
                action="etag_match",
            )
            return CrawlResult(
                url=url,
                content="",
                crawl_timestamp=crawl_timestamp,
                content_hash="",
                etag=head_etag,
                last_modified=head_last_modified,
                status_code=status_code,
                is_changed=False,
            )

        # Step 2: Full crawl via Firecrawl
        markdown = await self._firecrawl(url)

        # Step 3: Content hash comparison
        new_hash = _hash_content(markdown)
        cached_hash = await self._cache.get_content_hash(url, date_bucket)

        if cached_hash and cached_hash == new_hash:
            # Update ETag even if content unchanged
            if head_etag:
                await self._cache.set_etag(url, head_etag)
            log.info(
                "content_unchanged_hash",
                source=url,
                status="skip",
                action="hash_match",
            )
            return CrawlResult(
                url=url,
                content="",
                crawl_timestamp=crawl_timestamp,
                content_hash=new_hash,
                etag=head_etag,
                last_modified=head_last_modified,
                status_code=200,
                is_changed=False,
            )

        # Step 4: Content changed — update cache
        await self._cache.set_content_hash(url, date_bucket, new_hash)
        if head_etag:
            await self._cache.set_etag(url, head_etag)

        if self._circuit_breaker:
            await self._circuit_breaker.record_success(url)

        log.info(
            "crawl_success",
            source=url,
            status="success",
            action="content_changed",
            content_length=len(markdown),
        )

        return CrawlResult(
            url=url,
            content=markdown,
            crawl_timestamp=crawl_timestamp,
            content_hash=new_hash,
            etag=head_etag,
            last_modified=head_last_modified,
            status_code=200,
            is_changed=True,
        )

    async def _http_head(self, url: str) -> tuple[str | None, str | None, int]:
        """Send HTTP HEAD to get caching headers. Returns (etag, last_modified, status_code)."""
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                response = await client.head(url, follow_redirects=True)
                return (
                    response.headers.get("etag"),
                    response.headers.get("last-modified"),
                    response.status_code,
                )
        except httpx.TimeoutException:
            log.warning("http_head_timeout", source=url)
            return None, None, 0
        except httpx.RequestError as exc:
            log.warning("http_head_failed", source=url, error=str(exc))
            return None, None, 0

    async def _firecrawl(self, url: str) -> str:
        """Fetch URL via Firecrawl with retry on transient errors."""

        @retry(
            retry=retry_if_exception_type(CrawlError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            reraise=True,
        )
        async def _attempt() -> str:
            try:
                result = self._app.scrape_url(
                    url,
                    params={
                        "formats": ["markdown"],
                        "timeout": self._firecrawl_timeout * 1000,
                    },
                )
                if not result or not result.get("markdown"):
                    raise CrawlError(
                        code=ErrorCode.CRAWL_FAILED,
                        message=f"Firecrawl returned empty content for {url}",
                        context={"url": url, "result": str(result)[:200]},
                    )
                return result["markdown"]
            except CrawlError:
                raise
            except Exception as exc:
                error_str = str(exc).lower()
                if "blocked" in error_str or "403" in error_str or "bot" in error_str:
                    raise CrawlError(
                        code=ErrorCode.CRAWL_BLOCKED,
                        message=f"Source appears to be blocking crawlers: {url}",
                        context={"url": url},
                        cause=exc,
                    ) from exc
                if "timeout" in error_str:
                    raise CrawlError(
                        code=ErrorCode.CRAWL_TIMEOUT,
                        message=f"Firecrawl timed out on {url}",
                        context={"url": url},
                        cause=exc,
                    ) from exc
                raise CrawlError(
                    code=ErrorCode.CRAWL_FAILED,
                    message=f"Firecrawl failed for {url}: {exc}",
                    context={"url": url},
                    cause=exc,
                ) from exc

        try:
            return await _attempt()
        except RetryError as exc:
            if self._circuit_breaker:
                await self._circuit_breaker.record_failure(url, ErrorCode.CRAWL_FAILED)
            raise CrawlError(
                code=ErrorCode.CRAWL_FAILED,
                message=f"Firecrawl failed after {self._max_retries} retries: {url}",
                context={"url": url, "retries": self._max_retries},
                cause=exc,
            ) from exc
        except CrawlError as exc:
            if self._circuit_breaker:
                await self._circuit_breaker.record_failure(url, exc.code)
            raise
