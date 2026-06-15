"""Research Agent — decides what to crawl and dispatches crawl jobs.

Trigger: Daily at 2 AM via APScheduler.
Input: List of CompetitorConfig objects from the source registry.
Output: List of CrawlResult objects (changed content only).

Source types handled:
  firecrawl — JS-rendered page crawl via Firecrawl API
  rss       — RSS/Atom feed fetch via RSSCrawler
  tavily    — Targeted news search query via Tavily API
  apify     — Skipped with a warning (requires APIFY_API_KEY + tool implementation)

Guarantees:
- Every source is attempted.
- Failed sources are logged with named error codes and do not block others.
- Circuit-broken sources are skipped with CIRCUIT_OPEN logged.
- State is checkpointed to MongoDB after each source completes.

Single responsibility: decides what to crawl and returns raw crawl results.
Does NOT extract events — that is the Extraction Agent's job.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import trace_span
from schemas.config import CompetitorConfig
from schemas.state import AgentStatus, CrawlResult, PipelineState
from storage.cache import CacheStore
from storage.event_store import EventStore
from tools.apify import ApifyClient, ApifyError
from tools.crawler import CircuitBreaker, Crawler
from tools.errors import CircuitOpenError, CrawlError, ErrorCode

log = get_logger("research_agent")

_TAVILY_DAYS_BY_FREQUENCY = {
    "30min": 1,
    "daily": 7,
    "weekly": 30,
}


class ResearchAgent(BaseAgent):
    """Dispatches crawl jobs for all tracked competitor sources.

    Only crawls sources whose frequency matches the current schedule tick.
    Returns CrawlResult objects for sources with changed content.
    """

    name = "research_agent"
    description = (
        "Dispatches scheduled crawl jobs for all tracked competitor sources. "
        "Triggered daily at 2 AM. Returns changed content only. "
        "Guarantees: all sources attempted, failures logged, circuit breakers respected."
    )

    def __init__(
        self,
        crawler: Crawler,
        event_store: EventStore,
        cache: CacheStore,
        circuit_breaker: CircuitBreaker,
        tavily_search=None,
        apify_client: ApifyClient | None = None,
    ) -> None:
        self._crawler = crawler
        self._event_store = event_store
        self._cache = cache
        self._circuit_breaker = circuit_breaker
        self._tavily = tavily_search
        self._apify = apify_client

    async def run(
        self, competitors: list[CompetitorConfig], run_id: str | None = None
    ) -> list[CrawlResult]:
        """Crawl all sources for all competitors.

        Handles firecrawl, rss, and tavily source types concurrently.
        Apify sources are logged as not-yet-implemented and skipped.
        Returns only CrawlResult objects where is_changed=True.
        """
        run_id = run_id or str(uuid.uuid4())

        log.info(
            "research_agent_started",
            action="run",
            run_id=run_id,
            competitor_count=len(competitors),
            status="running",
        )

        crawl_tasks = []
        tavily_tasks = []
        apify_tasks = []

        for competitor in competitors:
            for source in competitor.sources:
                if source.type in ("firecrawl", "rss"):
                    if not source.url:
                        continue
                    crawl_tasks.append(
                        self._crawl_source(
                            competitor=competitor.competitor,
                            source_url=source.url,
                            run_id=run_id,
                        )
                    )

                elif source.type == "tavily":
                    query = source.apify_query  # alias="query" in SourceConfig
                    if not query:
                        continue
                    if not self._tavily:
                        log.warning(
                            "tavily_source_skipped",
                            action="run",
                            run_id=run_id,
                            competitor=competitor.competitor,
                            reason="TavilySearch not configured (TAVILY_API_KEY missing)",
                        )
                        continue
                    days = _TAVILY_DAYS_BY_FREQUENCY.get(source.frequency, 7)
                    tavily_tasks.append(
                        self._crawl_tavily_source(
                            competitor=competitor.competitor,
                            query=query,
                            days=days,
                            run_id=run_id,
                        )
                    )

                elif source.type == "apify":
                    if not self._apify:
                        log.warning(
                            "apify_source_skipped",
                            action="run",
                            run_id=run_id,
                            competitor=competitor.competitor,
                            actor=source.apify_actor,
                            reason="APIFY_API_KEY not configured",
                        )
                        continue
                    apify_tasks.append(
                        self._crawl_apify_source(
                            competitor=competitor.competitor,
                            source=source,
                            run_id=run_id,
                        )
                    )

        # Run firecrawl/rss crawls concurrently, bounded to avoid rate limits
        semaphore = asyncio.Semaphore(5)

        async def bounded_crawl(coro):
            async with semaphore:
                return await coro

        # Apify runs are already I/O-bound (thread pool) — use a tighter semaphore
        apify_semaphore = asyncio.Semaphore(2)

        async def bounded_apify(coro):
            async with apify_semaphore:
                return await coro

        all_tasks = (
            [bounded_crawl(t) for t in crawl_tasks]
            + [bounded_crawl(t) for t in tavily_tasks]
            + [bounded_apify(t) for t in apify_tasks]
        )
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        changed_results: list[CrawlResult] = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is None:
                continue
            # Tavily tasks return list[CrawlResult]; crawl tasks return CrawlResult | None
            if isinstance(result, list):
                changed_results.extend(r for r in result if r is not None and r.is_changed)
            elif result.is_changed:
                changed_results.append(result)

        log.info(
            "research_agent_completed",
            action="run",
            run_id=run_id,
            status="completed",
            firecrawl_rss_tasks=len(crawl_tasks),
            tavily_tasks=len(tavily_tasks),
            apify_tasks=len(apify_tasks),
            changed_sources=len(changed_results),
        )

        return changed_results

    async def _crawl_source(
        self, competitor: str, source_url: str, run_id: str
    ) -> CrawlResult | None:
        """Crawl a single firecrawl/rss source URL. Handles all error cases explicitly."""
        pipeline_state = PipelineState(
            run_id=f"{run_id}:{source_url}",
            started_at=datetime.now(tz=timezone.utc).isoformat(),
            status=AgentStatus.RUNNING,
            competitor=competitor,
            source_url=source_url,
            current_step="crawl",
        )

        async with trace_span(self.name, "crawl_source", run_id=run_id) as span:
            try:
                result = await self._crawler.crawl(source_url)
                pipeline_state.status = AgentStatus.COMPLETED
                pipeline_state.crawl_result = result
                pipeline_state.current_step = "crawl_complete"

                await self._event_store.upsert_pipeline_state(
                    pipeline_state.run_id, pipeline_state.model_dump()
                )

                if not result.is_changed:
                    log.info(
                        "source_unchanged",
                        action="crawl",
                        source=source_url,
                        competitor=competitor,
                        status="skip",
                    )
                else:
                    log.info(
                        "source_crawled",
                        action="crawl",
                        source=source_url,
                        competitor=competitor,
                        status="success",
                    )

                return result

            except CircuitOpenError as exc:
                log.warning(
                    "circuit_open_skipped",
                    error_code=ErrorCode.CIRCUIT_OPEN,
                    action="crawl",
                    source=source_url,
                    competitor=competitor,
                    status="skip",
                )
                pipeline_state.status = AgentStatus.FAILED
                pipeline_state.error_code = ErrorCode.CIRCUIT_OPEN
                pipeline_state.error_message = str(exc)
                await self._event_store.upsert_pipeline_state(
                    pipeline_state.run_id, pipeline_state.model_dump()
                )
                span.record_error(ErrorCode.CIRCUIT_OPEN, str(exc))
                return None

            except CrawlError as exc:
                log.error(
                    "crawl_failed",
                    error_code=exc.code,
                    action="crawl",
                    source=source_url,
                    competitor=competitor,
                    status="failure",
                    error=exc.message,
                )
                pipeline_state.status = AgentStatus.FAILED
                pipeline_state.error_code = exc.code
                pipeline_state.error_message = exc.message
                await self._event_store.upsert_pipeline_state(
                    pipeline_state.run_id, pipeline_state.model_dump()
                )
                span.record_error(exc.code, exc.message)
                return None

            except Exception as exc:
                log.error(
                    "crawl_unexpected_error",
                    error_code=ErrorCode.CRAWL_FAILED,
                    action="crawl",
                    source=source_url,
                    competitor=competitor,
                    status="failure",
                    error=str(exc),
                )
                pipeline_state.status = AgentStatus.FAILED
                pipeline_state.error_code = ErrorCode.CRAWL_FAILED
                pipeline_state.error_message = str(exc)
                await self._event_store.upsert_pipeline_state(
                    pipeline_state.run_id, pipeline_state.model_dump()
                )
                span.record_error(ErrorCode.CRAWL_FAILED, str(exc))
                return None

    async def _crawl_tavily_source(
        self,
        competitor: str,
        query: str,
        days: int,
        run_id: str,
    ) -> list[CrawlResult]:
        """Run a Tavily search query and return results as CrawlResult objects.

        Each result is tagged with `company` so the extraction pipeline can
        resolve the competitor without URL matching.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        # Build a set of DISTINCTIVE name tokens — generic words like "company",
        # "consulting", "group" appear in almost every article and produce false positives.
        _GENERIC_WORDS = {
            "the", "and", "inc", "llc", "ltd", "group", "company", "consulting",
            "advisory", "strategy", "digital", "global", "international", "management",
            "services", "partners", "firm", "solutions",
        }
        name_tokens = {
            t.lower() for t in competitor.replace("&", "").replace(",", "").split()
            if len(t) > 2 and t.lower() not in _GENERIC_WORDS
        }
        try:
            search_results = await self._tavily.search(
                query=query,
                max_results=10,
                days=days,
            )
            crawl_results: list[CrawlResult] = []
            skipped = 0
            for r in search_results:
                url = getattr(r, "url", None) or ""
                title = getattr(r, "title", None) or ""
                content_body = getattr(r, "content", None) or ""
                if not url or not content_body:
                    continue
                # Only keep articles that actually mention the target company
                combined_lower = (title + " " + content_body).lower()
                if not any(tok in combined_lower for tok in name_tokens):
                    skipped += 1
                    continue
                content = f"{title}\n\n{content_body}"
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                crawl_results.append(CrawlResult(
                    url=url,
                    content=content,
                    crawl_timestamp=now,
                    content_hash=content_hash,
                    status_code=200,
                    is_changed=True,
                    company=competitor,
                ))
            log.info(
                "tavily_source_crawled",
                action="crawl",
                competitor=competitor,
                query=query[:80],
                results=len(crawl_results),
                skipped_off_topic=skipped,
                run_id=run_id,
                status="success",
            )
            return crawl_results
        except Exception as exc:
            log.error(
                "tavily_source_failed",
                error_code=ErrorCode.CRAWL_FAILED,
                action="crawl",
                competitor=competitor,
                query=query[:80],
                run_id=run_id,
                status="failure",
                error=str(exc),
            )
            return []

    async def _crawl_apify_source(
        self,
        competitor: str,
        source,  # SourceConfig
        run_id: str,
    ) -> list[CrawlResult]:
        """Run an Apify actor and convert results to CrawlResult objects.

        LinkedIn posts and job listings → content text → ExtractionAgent.
        Reviews (G2/Capterra) → stored via event_store as raw review batches
        for the SentimentAgent to process separately.
        """
        from tools.apify import ACTOR_IDS

        actor = source.apify_actor or ""
        now = datetime.now(tz=timezone.utc).isoformat()

        try:
            # Determine which convenience method to call based on actor id
            if "linkedin" in actor:
                handle = source.apify_handle or competitor.lower().replace(" ", "-")
                result = await self._apify.scrape_linkedin(handle)
                return self._linkedin_items_to_crawl_results(result.items, competitor, now)

            elif "indeed" in actor:
                query = source.apify_query or competitor
                max_items = source.max_results or 5
                result = await self._apify.scrape_indeed_jobs(query, max_items=max_items)
                return self._job_items_to_crawl_results(result.items, competitor, "Indeed", now)

            elif "glassdoor" in actor:
                query = source.apify_query or competitor
                result = await self._apify.scrape_glassdoor_jobs(query)
                return self._job_items_to_crawl_results(result.items, competitor, "Glassdoor", now)

            elif "g2" in actor:
                slug = source.apify_slug or ""
                if not slug:
                    log.warning("apify_g2_no_slug", competitor=competitor)
                    return []
                result = await self._apify.scrape_g2_reviews(slug)
                await self._store_review_batch(result.items, competitor, "g2", now)
                return []  # Reviews go to SentimentAgent, not ExtractionAgent

            elif "capterra" in actor:
                slug = source.apify_slug or ""
                if not slug:
                    log.warning("apify_capterra_no_slug", competitor=competitor)
                    return []
                result = await self._apify.scrape_capterra_reviews(slug)
                await self._store_review_batch(result.items, competitor, "capterra", now)
                return []

            else:
                log.warning(
                    "apify_unknown_actor",
                    actor=actor,
                    competitor=competitor,
                    run_id=run_id,
                )
                return []

        except ApifyError as exc:
            log.error(
                "apify_source_failed",
                error_code=exc.code,
                actor=actor,
                competitor=competitor,
                run_id=run_id,
                error=exc.message,
            )
            return []
        except Exception as exc:
            log.error(
                "apify_source_unexpected_error",
                error_code=ErrorCode.CRAWL_FAILED,
                actor=actor,
                competitor=competitor,
                run_id=run_id,
                error=str(exc),
            )
            return []

    def _linkedin_items_to_crawl_results(
        self, items: list[dict], competitor: str, now: str
    ) -> list[CrawlResult]:
        results = []
        for item in items:
            text = item.get("text") or item.get("commentary") or ""
            url = item.get("url") or item.get("postUrl") or ""
            if not text or not url:
                continue
            content = f"LinkedIn post by {competitor}:\n\n{text}"
            results.append(CrawlResult(
                url=url,
                content=content,
                crawl_timestamp=now,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                status_code=200,
                is_changed=True,
                company=competitor,
            ))
        return results

    def _job_items_to_crawl_results(
        self, items: list[dict], competitor: str, platform: str, now: str
    ) -> list[CrawlResult]:
        results = []
        for item in items:
            title = item.get("title") or item.get("jobTitle") or ""
            description = item.get("description") or item.get("jobDescription") or ""
            url = item.get("url") or item.get("jobUrl") or ""
            if not title:
                continue
            content = f"{platform} job at {competitor}: {title}\n\n{description}"
            results.append(CrawlResult(
                url=url or f"https://{platform.lower()}.com/jobs/{hashlib.md5(content.encode()).hexdigest()[:8]}",
                content=content,
                crawl_timestamp=now,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                status_code=200,
                is_changed=True,
                company=competitor,
            ))
        return results

    async def _store_review_batch(
        self, items: list[dict], competitor: str, platform: str, now: str
    ) -> None:
        """Store raw review items for the SentimentAgent to process via ABSA."""
        if not items:
            return
        batch_doc = {
            "company": competitor,
            "platform": platform,
            "crawled_at": now,
            "review_count": len(items),
            "reviews": [
                {
                    "rating": item.get("rating") or item.get("overallRating"),
                    "title": item.get("title") or item.get("reviewTitle"),
                    "text": item.get("text") or item.get("reviewText") or item.get("body") or "",
                    "date": item.get("date") or item.get("reviewDate"),
                }
                for item in items
                if (item.get("text") or item.get("reviewText") or item.get("body"))
            ],
        }
        try:
            await self._event_store.store_review_batch(batch_doc)
            log.info(
                "review_batch_stored",
                competitor=competitor,
                platform=platform,
                count=len(batch_doc["reviews"]),
            )
        except Exception as exc:
            log.warning(
                "review_batch_store_failed",
                competitor=competitor,
                platform=platform,
                error=str(exc)[:120],
            )

    async def health_check(self) -> dict:
        cache_ok = await self._cache.health_check()
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if (cache_ok and store_ok) else "degraded",
            "dependencies": {
                "cache": "ok" if cache_ok else "failed",
                "event_store": "ok" if store_ok else "failed",
            },
        }
