"""Narrative Agent — detects strategic stories from event clusters (Sunday 5AM).

Algorithm:
1. Fetch all events for each competitor from last 90 days.
2. Embed event summaries.
3. Cluster with DBSCAN (min_samples=3, cosine distance metric).
4. Each cluster with ≥3 events → Sonnet synthesis: "What strategic story do these tell?"
5. Store NarrativeEvent with constituent_event_ids.

6-hour visibility window: only process events with timestamp < now() - 6h
to prevent half-ingested data entering synthesis.

Uses Sonnet (synthesis task, not extraction).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import normalize

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.narrative import NARRATIVE_SYSTEM, build_narrative_user_prompt
from schemas.events import NarrativeEvent
from storage.event_store import EventStore
from tools.embedder import Embedder
from tools.errors import AgentError, ErrorCode
from tools.llm_adapter import LLMAdapter

log = get_logger("narrative_agent")

_LOOKBACK_DAYS = 90
_MIN_CLUSTER_SIZE = 3
_DBSCAN_EPS = 0.15
_VISIBILITY_WINDOW_HOURS = 6


class NarrativeAgent(BaseAgent):
    """Detects strategic narratives by clustering competitor events.

    DBSCAN clusters of ≥3 semantically similar events are synthesised into
    narrative summaries by Sonnet. Output: NarrativeEvent per cluster.
    """

    name = "narrative_agent"
    description = (
        "Clusters competitor events by semantic similarity using DBSCAN, "
        "then synthesises each cluster into a strategic narrative using Sonnet. "
        "Schedule: Sunday 5AM. 6-hour visibility window to prevent half-ingested data."
    )

    def __init__(
        self,
        event_store: EventStore,
        embedder: Embedder,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
        min_cluster_size: int = _MIN_CLUSTER_SIZE,
        dbscan_eps: float = _DBSCAN_EPS,
        lookback_days: int = _LOOKBACK_DAYS,
    ) -> None:
        self._event_store = event_store
        self._embedder = embedder
        self._model_config = model_config
        self._cost_config = cost_config
        self._min_cluster_size = min_cluster_size
        self._dbscan_eps = dbscan_eps
        self._lookback_days = lookback_days
        self._llm_adapter = llm_adapter

    async def run(
        self,
        companies: list[str],
        run_id: str | None = None,
    ) -> list[NarrativeEvent]:
        """Detect and store narratives for all given companies."""
        run_id = run_id or str(uuid.uuid4())
        all_narratives: list[NarrativeEvent] = []

        for company in companies:
            try:
                narratives = await self._process_company(company, run_id)
                all_narratives.extend(narratives)
            except AgentError as exc:
                log.error(
                    "narrative_failed",
                    agent=self.name,
                    competitor=company,
                    error_code=exc.code,
                    error=exc.message,
                )
            except Exception as exc:
                log.error(
                    "narrative_unexpected_error",
                    agent=self.name,
                    competitor=company,
                    error_code=ErrorCode.AGENT_STATE_INVALID,
                    error=str(exc),
                )

        log.info(
            "narrative_completed",
            agent=self.name,
            action="run",
            run_id=run_id,
            companies=len(companies),
            narratives=len(all_narratives),
            status="completed",
        )
        return all_narratives

    async def _process_company(
        self, company: str, run_id: str
    ) -> list[NarrativeEvent]:
        now = datetime.now(tz=timezone.utc)
        visibility_cutoff = (now - timedelta(hours=_VISIBILITY_WINDOW_HOURS)).isoformat()

        # Fetch events within visibility window
        events = await self._event_store.get_recent_events(
            company=company,
            days=self._lookback_days,
            min_confidence=0.7,
            limit=300,
        )
        # Apply 6-hour visibility window
        events = [e for e in events if e.get("timestamp", "") <= visibility_cutoff]

        if len(events) < self._min_cluster_size:
            log.info(
                "insufficient_events_for_narrative",
                agent=self.name,
                competitor=company,
                event_count=len(events),
            )
            return []

        # Embed event summaries
        summaries = [e.get("summary", "") for e in events]
        embeddings = await self._embedder.embed_batch(summaries)

        if not embeddings or len(embeddings) != len(events):
            log.error(
                "embedding_count_mismatch",
                agent=self.name,
                competitor=company,
                error_code=ErrorCode.EMBED_FAILED,
            )
            return []

        # Cluster with DBSCAN (cosine distance = 1 - cosine_similarity)
        clusters = _cluster_events(
            embeddings=embeddings,
            eps=self._dbscan_eps,
            min_samples=self._min_cluster_size,
        )

        narratives: list[NarrativeEvent] = []
        unique_cluster_ids = set(clusters) - {-1}  # -1 = noise points

        for cluster_id in unique_cluster_ids:
            cluster_events = [
                events[i] for i, c in enumerate(clusters) if c == cluster_id
            ]
            if len(cluster_events) < self._min_cluster_size:
                continue

            narrative = await self._synthesise_narrative(
                company=company,
                cluster_events=cluster_events,
            )

            if narrative and narrative.confidence >= 0.6:
                await self._store_narrative(narrative)
                narratives.append(narrative)

        log.info(
            "narratives_generated",
            agent=self.name,
            competitor=company,
            event_count=len(events),
            clusters=len(unique_cluster_ids),
            narratives=len(narratives),
        )
        return narratives

    async def _synthesise_narrative(
        self, company: str, cluster_events: list[dict]
    ) -> NarrativeEvent | None:
        model = self._model_config.get("synthesis", "claude-sonnet-4-6")
        user_prompt = build_narrative_user_prompt(
            company=company,
            events=cluster_events,
            time_window_days=self._lookback_days,
        )

        class NarrativeSchema(BaseModel):
            narrative_title: str
            narrative_summary: str
            strategic_intent: str
            confidence: float = Field(..., ge=0.0, le=0.95)
            key_signals: list[str]

        async with trace_span(self.name, "synthesise_narrative") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=800,
                    messages=[
                        {"role": "system", "content": NARRATIVE_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=NarrativeSchema,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 200, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=len(user_prompt) // 4,
                    output_tokens=200,
                    cost_usd=cost,
                )

                constituent_ids = [
                    str(e.get("_id", "")) for e in cluster_events if e.get("_id")
                ]

                narrative = NarrativeEvent(
                    company=company,
                    narrative_title=result.narrative_title,
                    narrative_summary=result.narrative_summary,
                    constituent_event_ids=constituent_ids,
                    time_window_days=self._lookback_days,
                    confidence=result.confidence,
                    generated_date=datetime.now(tz=timezone.utc).isoformat(),
                    stakeholder_tags=["ceo", "marketing", "product"],
                )
                # key_signals and strategic_intent come from NarrativeSchema but
                # don't exist on NarrativeEvent — carry them as extra doc fields
                narrative._extra_fields = {  # type: ignore[attr-defined]
                    "key_signals": result.key_signals,
                    "strategic_intent": result.strategic_intent,
                }
                return narrative
            except Exception as exc:
                log.error(
                    "narrative_synthesis_failed",
                    agent=self.name,
                    competitor=company,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                return None

    async def _store_narrative(self, narrative: NarrativeEvent) -> None:
        doc = narrative.model_dump()
        doc["event_type"] = "narrative"
        doc["timestamp"] = narrative.generated_date
        doc["source_urls"] = []
        doc["confidence_score"] = narrative.confidence
        doc["summary"] = f"{narrative.company}: {narrative.narrative_title} — {narrative.narrative_summary[:150]}"
        # Merge extra fields computed during synthesis (key_signals, strategic_intent)
        extra = getattr(narrative, "_extra_fields", {})
        doc.update(extra)
        await self._event_store.insert_event(doc)
        log.info(
            "narrative_stored",
            agent=self.name,
            competitor=narrative.company,
            title=narrative.narrative_title,
            confidence=narrative.confidence,
            constituent_events=len(narrative.constituent_event_ids),
            status="ok",
        )

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if store_ok else "degraded",
            "dependencies": {"event_store": "ok" if store_ok else "failed"},
        }


def _cluster_events(
    embeddings: list[list[float]],
    eps: float,
    min_samples: int,
) -> list[int]:
    """DBSCAN clustering on normalised embeddings (cosine distance)."""
    matrix = np.array(embeddings, dtype=np.float32)
    matrix = normalize(matrix, norm="l2")
    # Cosine distance = 1 - cosine_similarity; eps in cosine distance space
    db = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine")
    labels = db.fit_predict(matrix)
    return labels.tolist()
