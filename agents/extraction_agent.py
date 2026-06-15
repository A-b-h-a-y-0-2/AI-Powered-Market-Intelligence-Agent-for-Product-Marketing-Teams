"""Extraction Agent — converts raw crawl results into typed, structured events.

Trigger: Triggered by Research Agent after each crawl batch completes.
Input: CrawlResult (raw markdown + metadata) + company name.
Output: ExtractionResult (list of stored event IDs + quarantine count + cost).

Three-pass pipeline per document:
  Pass 1 — Pre-filter (Haiku): Is this market-relevant? Skip if not.
  Pass 2 — Extraction (Groq + Instructor): Convert markdown → Pydantic event.
  Pass 3 — Judge (Sonnet, conditional): Validate low-confidence extractions.

After extraction:
  - Deduplication: cosine similarity vs. recent events for same company.
  - Quarantine: low-confidence events go to quarantine collection, not event store.
  - Embedding: store event summary embedding in Supabase pgvector.
  - Stakeholder tagging: assign tags at write time for fast persona-filtered queries.

Single responsibility: extraction and storage of events.
Does NOT decide what to crawl, does NOT generate insights.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Any, Literal

from pydantic import BaseModel

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.extraction import (
    EXTRACTION_SYSTEM,
    JUDGE_SYSTEM,
    PRE_FILTER_SYSTEM,
    PRE_FILTER_USER,
    build_extraction_user_prompt,
    build_judge_user_prompt,
)
from schemas.events import (
    AcquisitionEvent,
    FeatureLaunchEvent,
    FundingEvent,
    HiringTrendEvent,
    MarketTrendEvent,
    PartnershipEvent,
    PricingChangeEvent,
    ProductUpdateEvent,
)
from schemas.state import AgentStatus, CrawlResult, ExtractionResult, QuarantinedEvent
from storage.cache import CacheStore
from storage.event_store import EventStore
from storage.vector_store import VectorStore
from tools.embedder import Embedder
from tools.errors import ErrorCode, ExtractionError, LLMError
from tools.llm_adapter import LLMAdapter

log = get_logger("extraction_agent")

_EXTRACTABLE_TYPES = {
    "feature_launch": FeatureLaunchEvent,
    "product_update": ProductUpdateEvent,
    "pricing_change": PricingChangeEvent,
    "funding_event": FundingEvent,
    "acquisition": AcquisitionEvent,
    "partnership": PartnershipEvent,
    "hiring_trend": HiringTrendEvent,
    "market_trend": MarketTrendEvent,
}

_CONFIDENCE_QUARANTINE_THRESHOLD = 0.70
_DEDUP_SIMILARITY_THRESHOLD = 0.88
_DEDUP_LOOKBACK_DAYS = 7


class PreFilterResult(BaseModel):
    relevant: bool
    likely_event_type: Literal[
        "feature_launch", "pricing_change", "funding_event", "acquisition",
        "partnership", "hiring_trend", "product_update", "market_trend", "none",
    ]
    reason: str


class JudgeResult(BaseModel):
    confidence: str
    event_type_correct: bool
    summary_accurate: bool
    hallucinated_fields: list[str]
    issues: list[str]
    recommended_action: str


class ExtractionAgent(BaseAgent):
    """Converts crawl results into typed events stored in MongoDB + pgvector."""

    name = "extraction_agent"
    description = (
        "Converts raw markdown crawl results into typed Pydantic events. "
        "Three-pass pipeline: pre-filter → extraction → conditional judge. "
        "Handles deduplication, quarantine, embedding, and stakeholder tagging. "
        "Triggered after Research Agent completes a crawl batch."
    )

    def __init__(
        self,
        event_store: EventStore,
        vector_store: VectorStore,
        cache: CacheStore,
        embedder: Embedder,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
        confidence_threshold: float = _CONFIDENCE_QUARANTINE_THRESHOLD,
        dedup_threshold: float = _DEDUP_SIMILARITY_THRESHOLD,
    ) -> None:
        self._event_store = event_store
        self._vector_store = vector_store
        self._cache = cache
        self._embedder = embedder
        self._model_config = model_config
        self._cost_config = cost_config
        self._llm_adapter = llm_adapter
        self._confidence_threshold = confidence_threshold
        self._dedup_threshold = dedup_threshold

    async def run(
        self,
        crawl_result: CrawlResult,
        company: str,
        run_id: str | None = None,
    ) -> ExtractionResult:
        """Process a single CrawlResult through the full extraction pipeline."""
        run_id = run_id or str(uuid.uuid4())

        if not crawl_result.is_changed or not crawl_result.content:
            return ExtractionResult(
                source_url=crawl_result.url,
                crawl_timestamp=crawl_result.crawl_timestamp,
                skipped_count=1,
            )

        log.info(
            "extraction_started",
            action="run",
            source=crawl_result.url,
            competitor=company,
            run_id=run_id,
            status="running",
        )

        result = ExtractionResult(
            source_url=crawl_result.url,
            crawl_timestamp=crawl_result.crawl_timestamp,
        )

        async with trace_span(self.name, "extract_document", run_id=run_id):
            # Pass 1: Pre-filter
            pre_filter = await self._pre_filter(
                company=company,
                source_url=crawl_result.url,
                crawl_date=crawl_result.crawl_timestamp[:10],
                content=crawl_result.content,
            )
            result.llm_cost_usd += pre_filter["cost_usd"]

            if not pre_filter["relevant"]:
                log.info(
                    "content_filtered_out",
                    action="pre_filter",
                    source=crawl_result.url,
                    competitor=company,
                    reason=pre_filter["reason"],
                    status="skip",
                )
                result.skipped_count += 1
                return result

            event_type_hint = pre_filter["likely_event_type"]
            if event_type_hint not in _EXTRACTABLE_TYPES:
                log.info(
                    "event_type_not_yet_supported",
                    action="pre_filter",
                    source=crawl_result.url,
                    event_type=event_type_hint,
                    status="skip",
                )
                result.skipped_count += 1
                return result

            # Pass 2: Extraction
            extraction = await self._extract_event(
                company=company,
                source_url=crawl_result.url,
                crawl_date=crawl_result.crawl_timestamp[:10],
                event_type_hint=event_type_hint,
                content=crawl_result.content,
            )
            result.llm_cost_usd += extraction["cost_usd"]

            if extraction["error"]:
                result.error_code = extraction["error_code"]
                result.error_message = extraction["error"]
                return result

            event_obj = extraction["event"]
            confidence = event_obj.confidence_score

            # Pass 3: Judge (only when confidence below threshold)
            if confidence < self._confidence_threshold:
                judge = await self._judge_event(
                    raw_content=crawl_result.content[:3000],
                    event_obj=event_obj,
                )
                result.llm_cost_usd += judge["cost_usd"]

                if judge["recommended_action"] in ("quarantine", "reject"):
                    await self._quarantine(
                        crawl_result=crawl_result,
                        event_obj=event_obj,
                        error_code=ErrorCode.EXTRACTION_LOW_CONFIDENCE,
                        error_details=json.dumps(judge["issues"]),
                    )
                    result.quarantined_count += 1
                    log.warning(
                        "event_quarantined",
                        action="judge",
                        source=crawl_result.url,
                        competitor=company,
                        confidence=confidence,
                        recommended_action=judge["recommended_action"],
                        status="quarantined",
                    )
                    return result

            # Deduplication check
            is_duplicate, existing_event_id = await self._deduplicate(
                event_obj=event_obj,
                company=company,
            )

            if is_duplicate and existing_event_id:
                await self._event_store.add_source_to_event(
                    existing_event_id, crawl_result.url
                )
                log.info(
                    "event_deduplicated",
                    action="dedup",
                    source=crawl_result.url,
                    competitor=company,
                    merged_into=existing_event_id,
                    status="merged",
                )
                return result

            # Assign stakeholder tags
            event_dict = self._assign_stakeholder_tags(event_obj.model_dump())

            # Store event
            event_id = await self._event_store.insert_event(event_dict)
            result.events_extracted.append(event_dict)

            # Embed and store in vector store (non-fatal — MongoDB write already succeeded)
            try:
                embedding = await self._embedder.embed(event_obj.summary)
                await self._vector_store.upsert_embedding(
                    event_id=event_id,
                    company=company,
                    event_type=event_obj.event_type.value,
                    timestamp=event_obj.timestamp,
                    summary=event_obj.summary,
                    embedding=embedding,
                    stakeholder_tags=event_dict.get("stakeholder_tags", []),
                )
            except Exception as embed_exc:
                log.warning(
                    "embedding_skipped",
                    action="store",
                    source=crawl_result.url,
                    competitor=company,
                    event_id=event_id,
                    reason=str(embed_exc)[:120],
                )

            log.info(
                "event_stored",
                action="store",
                source=crawl_result.url,
                competitor=company,
                event_type=event_obj.event_type.value,
                event_id=event_id,
                confidence=confidence,
                cost_usd=result.llm_cost_usd,
                status="success",
            )

        return result

    async def _pre_filter(
        self, company: str, source_url: str, crawl_date: str, content: str
    ) -> dict:
        model = self._model_config["pre_filter"]
        user_prompt = PRE_FILTER_USER.format(
            company=company,
            source_url=source_url,
            crawl_date=crawl_date,
            content_excerpt=content[:2000],
        )

        async with trace_span(self.name, "pre_filter") as span:
            try:
                _call = partial(
                    self._llm_adapter.get_instructor_client(model).chat.completions.create,
                    model=model,
                    max_tokens=256,
                    messages=[
                        {"role": "system", "content": PRE_FILTER_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=PreFilterResult,
                )
                result = await asyncio.get_event_loop().run_in_executor(None, _call)
                input_tokens = 512  # approximate; replace with actual usage when available
                output_tokens = 64
                cost = calculate_cost(model, input_tokens, output_tokens, self._cost_config)
                span.record_llm(model=model, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)
                return {
                    "relevant": result.relevant,
                    "likely_event_type": result.likely_event_type,
                    "reason": result.reason,
                    "cost_usd": cost,
                }
            except Exception as exc:
                log.error(
                    "pre_filter_failed",
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    source=source_url,
                    error=str(exc),
                )
                # On pre-filter failure, skip the document to be safe
                return {
                    "relevant": False,
                    "likely_event_type": "none",
                    "reason": f"pre_filter_error: {exc}",
                    "cost_usd": 0.0,
                }

    async def _extract_event(
        self,
        company: str,
        source_url: str,
        crawl_date: str,
        event_type_hint: str,
        content: str,
    ) -> dict:
        model = self._model_config["extraction"]
        schema_class = _EXTRACTABLE_TYPES[event_type_hint]
        user_prompt = build_extraction_user_prompt(
            company=company,
            source_url=source_url,
            crawl_date=crawl_date,
            event_type_hint=event_type_hint,
            content=content,
        )

        async with trace_span(self.name, "extract_event") as span:
            try:
                _call = partial(
                    self._llm_adapter.get_instructor_client(model).chat.completions.create,
                    model=model,
                    messages=[
                        {"role": "system", "content": EXTRACTION_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=schema_class,
                    max_retries=3,
                )
                event_obj = await asyncio.get_event_loop().run_in_executor(None, _call)
                # Ensure company and source_url are set correctly
                event_obj.company = company
                if source_url not in event_obj.source_urls:
                    event_obj.source_urls = [source_url]

                input_tokens = len(user_prompt) // 4  # rough approximation
                output_tokens = 256
                cost = calculate_cost(model, input_tokens, output_tokens, self._cost_config)
                span.record_llm(model=model, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

                return {"event": event_obj, "cost_usd": cost, "error": None, "error_code": None}

            except Exception as exc:
                log.error(
                    "extraction_failed",
                    error_code=ErrorCode.EXTRACTION_INVALID_SCHEMA,
                    source=source_url,
                    competitor=company,
                    event_type=event_type_hint,
                    error=str(exc),
                )
                return {
                    "event": None,
                    "cost_usd": 0.0,
                    "error": str(exc),
                    "error_code": ErrorCode.EXTRACTION_INVALID_SCHEMA,
                }

    async def _judge_event(self, raw_content: str, event_obj: Any) -> dict:
        model = self._model_config["validation"]
        user_prompt = build_judge_user_prompt(
            raw_content_excerpt=raw_content,
            extracted_event_json=event_obj.model_dump_json(indent=2),
        )

        async with trace_span(self.name, "judge_event") as span:
            try:
                _call = partial(
                    self._llm_adapter.get_instructor_client(model).chat.completions.create,
                    model=model,
                    max_tokens=512,
                    messages=[
                        {"role": "system", "content": JUDGE_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=JudgeResult,
                )
                result = await asyncio.get_event_loop().run_in_executor(None, _call)
                input_tokens = len(user_prompt) // 4
                output_tokens = 128
                cost = calculate_cost(model, input_tokens, output_tokens, self._cost_config)
                span.record_llm(model=model, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

                return {
                    "confidence": result.confidence,
                    "recommended_action": result.recommended_action,
                    "issues": result.issues,
                    "cost_usd": cost,
                }
            except Exception as exc:
                log.error(
                    "judge_failed",
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                # On judge failure, quarantine to be safe
                return {
                    "confidence": "low",
                    "recommended_action": "quarantine",
                    "issues": [f"judge_error: {exc}"],
                    "cost_usd": 0.0,
                }

    async def _deduplicate(
        self, event_obj: Any, company: str
    ) -> tuple[bool, str | None]:
        """Check if a semantically similar event already exists.

        Returns (is_duplicate, existing_event_id).
        """
        try:
            new_embedding = await self._embedder.embed(event_obj.summary)
            recent_events = await self._event_store.get_recent_events(
                company=company, days=_DEDUP_LOOKBACK_DAYS, limit=50
            )

            if not recent_events:
                return False, None

            # Get embeddings for recent events
            recent_summaries = [e.get("summary", "") for e in recent_events]
            recent_embeddings = await self._embedder.embed_batch(recent_summaries)

            # Cosine similarity
            max_similarity = 0.0
            best_match_id = None
            for i, emb in enumerate(recent_embeddings):
                similarity = _cosine_similarity(new_embedding, emb)
                if similarity > max_similarity:
                    max_similarity = similarity
                    best_match_id = str(recent_events[i].get("_id", ""))

            if max_similarity > self._dedup_threshold:
                return True, best_match_id

            return False, None

        except Exception as exc:
            log.warning(
                "dedup_failed",
                error_code=ErrorCode.DEDUP_MERGE_FAILED,
                competitor=company,
                error=str(exc),
            )
            # On dedup failure, store the event (false negative is better than data loss)
            return False, None

    async def _quarantine(
        self,
        crawl_result: CrawlResult,
        event_obj: Any,
        error_code: str,
        error_details: str,
    ) -> None:
        quarantine_doc = QuarantinedEvent(
            quarantine_id=str(uuid.uuid4()),
            source_url=crawl_result.url,
            raw_content_excerpt=crawl_result.content[:2000],
            extracted_event=event_obj.model_dump() if event_obj else {},
            confidence_score=getattr(event_obj, "confidence_score", 0.0),
            error_code=error_code,
            error_details=error_details,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        await self._event_store.insert_quarantined_event(quarantine_doc.model_dump())

    def _assign_stakeholder_tags(self, event_dict: dict) -> dict:
        """Assign stakeholder tags based on event type. Called at write time."""
        event_type = event_dict.get("event_type", "")
        tags: list[str] = []

        _TYPE_TO_TAGS: dict[str, list[str]] = {
            "feature_launch": ["product", "marketing", "sales"],
            "pricing_change": ["ceo", "sales", "customer_success", "marketing"],
            "funding_event": ["ceo", "marketing"],
            "acquisition": ["ceo", "product", "marketing"],
            "partnership": ["marketing", "sales", "product"],
            "hiring_trend": ["ceo", "product"],
            "product_update": ["product", "customer_success"],
            "market_trend": ["ceo", "marketing", "product"],
        }

        tags = _TYPE_TO_TAGS.get(event_type, [])
        event_dict["stakeholder_tags"] = tags
        return event_dict

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        cache_ok = await self._cache.health_check()
        return {
            "agent": self.name,
            "status": "ok" if (store_ok and cache_ok) else "degraded",
            "dependencies": {
                "event_store": "ok" if store_ok else "failed",
                "cache": "ok" if cache_ok else "failed",
            },
        }


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
