"""Sentiment Agent — ABSA on G2/Reddit/Capterra reviews.

Schedule: Daily 5AM (after Research + Extraction).
Input: Crawled review content per competitor per platform.
Output: CustomerSentimentEvent per (company, aspect) pair.

Uses predefined aspect taxonomy (v1) for consistency.
A single review produces one CustomerSentimentEvent per relevant aspect.
Mixed-sentiment reviews correctly produce multiple events.

Model: Groq (extraction) — ABSA is a classification task, not synthesis.
Judge pass (Sonnet) only when confidence < 0.7.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import instructor
import yaml
from groq import Groq
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.sentiment import ABSA_SYSTEM, build_absa_user_prompt
from schemas.events import CustomerSentimentEvent
from storage.event_store import EventStore
from tools.errors import AgentError, ErrorCode

log = get_logger("sentiment_agent")

_ASPECTS_PATH = Path("config/feature_taxonomy.yaml")
_CONFIDENCE_THRESHOLD = 0.70


def _load_aspects() -> tuple[list[str], dict[str, str]]:
    """Load aspect list from feature taxonomy. Returns (aspect_keys, descriptions)."""
    # Aspect taxonomy uses the feature taxonomy category names for consistency
    if not _ASPECTS_PATH.exists():
        # Hardcoded fallback if file missing
        aspects = [
            "ai_automation", "crm_integration", "analytics_reporting",
            "security_compliance", "pricing_packaging", "api_developer",
            "content_generation", "workflow_ux",
        ]
        return aspects, {a: a.replace("_", " ") for a in aspects}

    with open(_ASPECTS_PATH) as f:
        data = yaml.safe_load(f)
    cats = data.get("categories", {})
    keys = list(cats.keys())
    descs = {k: v.get("description", k) for k, v in cats.items()}
    return keys, descs


class AspectSentimentResult(BaseModel):
    aspect: str
    sentiment: str
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    representative_quote: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)


class ReviewBatch(BaseModel):
    company: str
    source_platform: str
    reviews: list[str]
    crawl_date: str


class SentimentAgent(BaseAgent):
    """Runs ABSA on product reviews, stores CustomerSentimentEvents."""

    name = "sentiment_agent"
    description = (
        "Aspect-Based Sentiment Analysis on G2/Reddit/Capterra reviews. "
        "Produces one CustomerSentimentEvent per (company, aspect) pair. "
        "Uses predefined taxonomy for consistency. Daily 5AM schedule."
    )

    def __init__(
        self,
        event_store: EventStore,
        model_config: dict,
        cost_config: dict,
        confidence_threshold: float = _CONFIDENCE_THRESHOLD,
    ) -> None:
        self._event_store = event_store
        self._model_config = model_config
        self._cost_config = cost_config
        self._confidence_threshold = confidence_threshold
        self._groq_client = instructor.from_groq(Groq())
        self._aspects, self._aspect_descriptions = _load_aspects()

    async def run(
        self,
        batches: list[ReviewBatch],
        run_id: str | None = None,
    ) -> list[CustomerSentimentEvent]:
        """Process review batches and store sentiment events.

        Args:
            batches: List of ReviewBatch objects (company + platform + reviews).
            run_id: Optional trace ID.

        Returns:
            List of stored CustomerSentimentEvent objects.
        """
        run_id = run_id or str(uuid.uuid4())
        all_events: list[CustomerSentimentEvent] = []

        for batch in batches:
            try:
                events = await self._process_batch(batch, run_id)
                all_events.extend(events)
            except AgentError as exc:
                log.error(
                    "sentiment_batch_failed",
                    agent=self.name,
                    competitor=batch.company,
                    platform=batch.source_platform,
                    error_code=exc.code,
                    error=exc.message,
                )
            except Exception as exc:
                log.error(
                    "sentiment_batch_unexpected_error",
                    agent=self.name,
                    competitor=batch.company,
                    platform=batch.source_platform,
                    error_code=ErrorCode.AGENT_STATE_INVALID,
                    error=str(exc),
                )

        log.info(
            "sentiment_completed",
            agent=self.name,
            action="run",
            run_id=run_id,
            batches=len(batches),
            events_stored=len(all_events),
            status="completed",
        )
        return all_events

    async def _process_batch(
        self, batch: ReviewBatch, run_id: str
    ) -> list[CustomerSentimentEvent]:
        """Process a single review batch for one company+platform."""
        # Collect raw aspect results across all reviews in the batch
        raw_by_aspect: dict[str, list[AspectSentimentResult]] = {a: [] for a in self._aspects}

        for review_text in batch.reviews:
            if not review_text.strip():
                continue

            results = await self._extract_aspects(
                company=batch.company,
                source_platform=batch.source_platform,
                review_text=review_text,
                crawl_date=batch.crawl_date,
            )

            for r in results:
                if r.aspect in raw_by_aspect:
                    raw_by_aspect[r.aspect].append(r)

        # Aggregate per-aspect and create CustomerSentimentEvent
        events: list[CustomerSentimentEvent] = []
        date_range = f"up to {batch.crawl_date}"

        for aspect, aspect_results in raw_by_aspect.items():
            if not aspect_results:
                continue

            # Aggregate: weighted average of scores, take best quotes
            avg_score = sum(r.sentiment_score for r in aspect_results) / len(aspect_results)
            avg_confidence = sum(r.confidence_score for r in aspect_results) / len(aspect_results)

            if avg_confidence < self._confidence_threshold:
                log.info(
                    "aspect_low_confidence_skipped",
                    agent=self.name,
                    competitor=batch.company,
                    aspect=aspect,
                    confidence=avg_confidence,
                )
                continue

            sentiment = (
                "positive" if avg_score > 0.15
                else "negative" if avg_score < -0.15
                else "mixed"
            )

            top_quotes = [
                r.representative_quote
                for r in sorted(aspect_results, key=lambda x: abs(x.sentiment_score), reverse=True)
                if r.representative_quote
            ][:3]

            event = CustomerSentimentEvent(
                company=batch.company,
                source_platform=batch.source_platform,
                aspect=aspect,
                sentiment=sentiment,
                sentiment_score=round(avg_score, 3),
                representative_quotes=top_quotes,
                review_count=len(aspect_results),
                date_range=date_range,
                confidence_score=round(avg_confidence, 3),
                stakeholder_tags=["marketing", "product", "customer_success"],
            )

            # Store event
            doc = event.model_dump()
            doc["event_type"] = "customer_sentiment"
            doc["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
            doc["source_urls"] = []
            doc["summary"] = event.summary  # computed property — not in model_dump()
            await self._event_store.insert_event(doc)

            log.info(
                "sentiment_event_stored",
                agent=self.name,
                competitor=batch.company,
                platform=batch.source_platform,
                aspect=aspect,
                sentiment=sentiment,
                score=avg_score,
                review_count=len(aspect_results),
                status="ok",
            )
            events.append(event)

        return events

    async def _extract_aspects(
        self,
        company: str,
        source_platform: str,
        review_text: str,
        crawl_date: str,
    ) -> list[AspectSentimentResult]:
        model = self._model_config.get("extraction", "llama-3.3-70b-versatile")
        user_prompt = build_absa_user_prompt(
            company=company,
            source_platform=source_platform,
            review_text=review_text,
            aspects=self._aspects,
            aspect_descriptions=self._aspect_descriptions,
            crawl_date=crawl_date,
        )

        class AspectResultList(BaseModel):
            results: list[AspectSentimentResult] = Field(default_factory=list)

        async with trace_span(self.name, "extract_aspects") as span:
            try:
                # Groq doesn't support list response_model directly — wrap in container
                response = self._groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": ABSA_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=AspectResultList,
                    max_retries=2,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 256, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=len(user_prompt) // 4,
                    output_tokens=256,
                    cost_usd=cost,
                )
                return response.results
            except Exception as exc:
                log.error(
                    "absa_extraction_failed",
                    agent=self.name,
                    competitor=company,
                    platform=source_platform,
                    error_code=ErrorCode.EXTRACTION_INVALID_SCHEMA,
                    error=str(exc),
                )
                return []

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if store_ok else "degraded",
            "aspects_loaded": len(self._aspects),
            "dependencies": {"event_store": "ok" if store_ok else "failed"},
        }
