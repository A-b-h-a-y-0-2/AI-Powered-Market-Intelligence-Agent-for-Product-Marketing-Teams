"""Apify actor runner — scrapes LinkedIn, Indeed, Glassdoor, G2, Capterra.

Single responsibility: run a named Apify actor with typed input, return raw
item dicts. Callers (ResearchAgent) convert items to CrawlResult or ReviewBatch.

All calls have explicit timeouts and raise ApifyError with named codes on failure.
Idempotent: running the same actor with the same input twice returns equivalent results.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from observability.logger import get_logger
from tools.errors import AgentError, ErrorCode

log = get_logger("apify")

# Apify actor IDs we actually support — verified against account free trials
ACTOR_IDS = {
    # LinkedIn post search — curious_coder actor (free trial on account)
    "linkedin_company": "curious_coder/linkedin-post-search-scraper",
    # Indeed job listings — curious_coder actor (free trial on account)
    "indeed_jobs": "curious_coder/indeed-scraper",
    # Glassdoor job listings — paid, skipped for now
    "glassdoor_jobs": "apify/glassdoor-scraper",
    # G2 product reviews
    "g2_reviews": "voyager/g2-reviews-scraper",
    # Capterra product reviews
    "capterra_reviews": "apify/capterra-reviews-scraper",
}

# Actor-specific input builders — each returns the dict Apify expects
def _linkedin_input(handle: str, max_posts: int = 20) -> dict:
    # curious_coder/linkedin-post-search-scraper uses keyword search, not profile URL
    return {
        "keywords": handle.lstrip("@"),
        "maxItems": max_posts,
    }


def _indeed_input(query: str, max_items: int = 50) -> dict:
    return {
        "position": query,
        "country": "us",  # curious_coder actor requires lowercase ISO code
        "maxItems": max_items,
    }


def _glassdoor_input(query: str, max_items: int = 50) -> dict:
    return {
        "keyword": query,
        "country": "us",
        "maxResults": max_items,
    }


def _g2_input(product_slug: str, max_reviews: int = 50) -> dict:
    return {
        "productSlug": product_slug,
        "maxReviews": max_reviews,
    }


def _capterra_input(product_slug: str, max_reviews: int = 50) -> dict:
    return {
        "startUrl": f"https://www.capterra.com/p/{product_slug}/reviews/",
        "maxItems": max_reviews,
    }


class ApifyError(AgentError):
    """Raised when an Apify actor run fails."""


class ApifyRunResult(BaseModel):
    actor_id: str
    run_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    item_count: int = 0


class ApifyClient:
    """Async wrapper around the Apify client SDK.

    Dependency-injected — never creates its own event loop or global state.
    Times out at `default_timeout_secs` per actor run.
    """

    def __init__(self, api_token: str, default_timeout_secs: int = 300) -> None:
        self._token = api_token
        self._timeout = default_timeout_secs

    async def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        timeout_secs: int | None = None,
        memory_mbytes: int = 512,
    ) -> ApifyRunResult:
        """Run an Apify actor and return all dataset items.

        Runs in a thread pool so it doesn't block the event loop.
        Raises ApifyError with CRAWL_FAILED on any failure.
        """
        timeout = timeout_secs or self._timeout
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._run_sync(actor_id, run_input, memory_mbytes),
                ),
                timeout=timeout,
            )
            log.info(
                "apify_actor_completed",
                actor=actor_id,
                item_count=result.item_count,
                run_id=result.run_id,
                status="success",
            )
            return result
        except asyncio.TimeoutError:
            raise ApifyError(
                code=ErrorCode.SOURCE_TIMEOUT,
                message=f"Apify actor {actor_id!r} timed out after {timeout}s",
            )
        except ApifyError:
            raise
        except Exception as exc:
            raise ApifyError(
                code=ErrorCode.CRAWL_FAILED,
                message=f"Apify actor {actor_id!r} failed: {exc}",
            ) from exc

    def _run_sync(
        self, actor_id: str, run_input: dict[str, Any], memory_mbytes: int
    ) -> ApifyRunResult:
        """Synchronous Apify run — called from thread pool executor."""
        from apify_client import ApifyClient as _SDK

        client = _SDK(self._token)
        run = client.actor(actor_id).call(
            run_input=run_input,
            memory_mbytes=memory_mbytes,
        )
        if not run:
            raise ApifyError(
                code=ErrorCode.CRAWL_FAILED,
                message=f"Actor {actor_id!r} returned no run object",
            )
        # SDK v1 returns dict; v2+ returns typed Run object — handle both
        if isinstance(run, dict):
            status = run.get("status", "UNKNOWN")
            run_id = run["id"]
            dataset_id = run["defaultDatasetId"]
        else:
            status = getattr(run, "status", "UNKNOWN")
            run_id = getattr(run, "id", None) or run.get("id", "")
            dataset_id = getattr(run, "default_dataset_id", None) or run.get("defaultDatasetId", "")
        if status not in ("SUCCEEDED",):
            raise ApifyError(
                code=ErrorCode.CRAWL_FAILED,
                message=f"Actor {actor_id!r} finished with status {status!r}",
            )
        items = list(client.dataset(dataset_id).iterate_items())
        return ApifyRunResult(actor_id=actor_id, run_id=run_id, items=items, item_count=len(items))

    # ── Convenience methods for each supported source type ────────────────────

    async def scrape_linkedin(self, handle: str, max_posts: int = 20) -> ApifyRunResult:
        """Search LinkedIn posts by company keyword (curious_coder/linkedin-post-search-scraper)."""
        return await self.run_actor(
            ACTOR_IDS["linkedin_company"],
            _linkedin_input(handle, max_posts),
        )

    async def scrape_indeed_jobs(self, query: str, max_items: int = 50) -> ApifyRunResult:
        return await self.run_actor(
            ACTOR_IDS["indeed_jobs"],
            _indeed_input(query, max_items),
            memory_mbytes=1024,
        )

    async def scrape_glassdoor_jobs(self, query: str, max_items: int = 50) -> ApifyRunResult:
        return await self.run_actor(
            ACTOR_IDS["glassdoor_jobs"],
            _glassdoor_input(query, max_items),
            memory_mbytes=1024,
        )

    async def scrape_g2_reviews(self, product_slug: str, max_reviews: int = 50) -> ApifyRunResult:
        return await self.run_actor(
            ACTOR_IDS["g2_reviews"],
            _g2_input(product_slug, max_reviews),
        )

    async def scrape_capterra_reviews(
        self, product_slug: str, max_reviews: int = 50
    ) -> ApifyRunResult:
        return await self.run_actor(
            ACTOR_IDS["capterra_reviews"],
            _capterra_input(product_slug, max_reviews),
        )
