"""Convergence Agent — cross-competitor market trend detection (Sunday 6AM).

Algorithm:
1. Fetch FeatureLaunch + ProductUpdate events across ALL competitors (last 60 days).
2. Embed all events together.
3. Cluster across company boundaries using DBSCAN.
4. Any cluster where ≥3 different companies contribute → emerging market trend.
5. Sonnet synthesis: "What trend do these cross-company events represent?"
6. Store as MarketTrendEvent with company diversity as confidence signal.

Company diversity is the key signal: one company doing something is a product decision.
Three companies doing the same thing is a market shift.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import numpy as np
from pydantic import BaseModel
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import normalize

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.narrative import CONVERGENCE_SYSTEM, build_convergence_user_prompt
from schemas.events import MarketTrendEvent
from storage.event_store import EventStore
from tools.embedder import Embedder
from tools.errors import ErrorCode
from tools.llm_adapter import LLMAdapter

log = get_logger("convergence_agent")

_LOOKBACK_DAYS = 60
_MIN_COMPANIES_FOR_TREND = 2  # Lowered from 3 — with small initial datasets, 3 is too strict
_DBSCAN_EPS = 0.35  # Raised from 0.20 — cross-company events on the same topic have cosine distance ~0.25-0.35
_MIN_CLUSTER_SIZE = 2
_TREND_EVENT_TYPES = [
    "feature_launch",
    "product_update",
    "pricing_change",
    "partnership",
]


class ConvergenceAgent(BaseAgent):
    """Cross-competitor cluster analysis to detect market-wide trends.

    Sunday 6AM. Requires events from multiple competitors to declare a trend.
    """

    name = "convergence_agent"
    description = (
        "Cross-competitor event clustering to detect market-wide trends. "
        "Requires ≥3 different companies in a cluster to declare a MarketTrendEvent. "
        "Schedule: Sunday 6AM."
    )

    def __init__(
        self,
        event_store: EventStore,
        embedder: Embedder,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
        min_companies: int = _MIN_COMPANIES_FOR_TREND,
        lookback_days: int = _LOOKBACK_DAYS,
    ) -> None:
        self._event_store = event_store
        self._embedder = embedder
        self._model_config = model_config
        self._cost_config = cost_config
        self._llm_adapter = llm_adapter
        self._min_companies = min_companies
        self._lookback_days = lookback_days

    async def run(
        self,
        companies: list[str],
        run_id: str | None = None,
    ) -> list[MarketTrendEvent]:
        """Detect cross-competitor trends and store MarketTrendEvents."""
        run_id = run_id or str(uuid.uuid4())

        # Fetch relevant events across ALL companies
        all_events: list[dict] = []
        for company in companies:
            events = await self._event_store.get_recent_events(
                company=company,
                days=self._lookback_days,
                event_types=_TREND_EVENT_TYPES,
                min_confidence=0.7,
                limit=100,
            )
            all_events.extend(events)

        if len(all_events) < self._min_companies * 2:
            log.info(
                "insufficient_cross_company_events",
                agent=self.name,
                action="run",
                run_id=run_id,
                event_count=len(all_events),
            )
            return []

        log.info(
            "convergence_started",
            agent=self.name,
            action="run",
            run_id=run_id,
            events=len(all_events),
            companies=len(companies),
        )

        # Embed all events
        summaries = [e.get("summary", "") for e in all_events]
        embeddings = await self._embedder.embed_batch(summaries)

        if not embeddings or len(embeddings) != len(all_events):
            log.error(
                "embedding_failed",
                agent=self.name,
                error_code=ErrorCode.EMBED_FAILED,
            )
            return []

        # Cluster across company boundaries
        cluster_labels = _cluster_events(
            embeddings=embeddings,
            eps=_DBSCAN_EPS,
            min_samples=_MIN_CLUSTER_SIZE,
        )

        # Find cross-company clusters
        trends: list[MarketTrendEvent] = []
        unique_clusters = set(cluster_labels) - {-1}

        for cluster_id in unique_clusters:
            cluster_events = [
                all_events[i]
                for i, c in enumerate(cluster_labels)
                if c == cluster_id
            ]
            companies_in_cluster = {e.get("company", "") for e in cluster_events}
            companies_in_cluster.discard("")

            if len(companies_in_cluster) < self._min_companies:
                continue

            # Enough diversity → synthesise trend
            events_by_company = {
                comp: [e for e in cluster_events if e.get("company") == comp]
                for comp in companies_in_cluster
            }

            trend = await self._synthesise_trend(events_by_company)
            if trend and trend.confidence >= 0.6:
                await self._store_trend(trend)
                trends.append(trend)

        log.info(
            "convergence_completed",
            agent=self.name,
            action="run",
            run_id=run_id,
            clusters_analysed=len(unique_clusters),
            trends_detected=len(trends),
            status="completed",
        )
        return trends

    async def _synthesise_trend(
        self, events_by_company: dict[str, list[dict]]
    ) -> MarketTrendEvent | None:
        model = self._model_config.get("synthesis", "claude-sonnet-4-6")
        user_prompt = build_convergence_user_prompt(
            events_by_company=events_by_company,
            time_window_days=self._lookback_days,
        )

        class TrendSchema(BaseModel):
            is_market_trend: bool
            trend_name: str
            trend_summary: str
            companies_involved: list[str]
            trend_strength: str
            confidence: float
            what_this_means: str

        async with trace_span(self.name, "synthesise_trend") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=600,
                    messages=[
                        {"role": "system", "content": CONVERGENCE_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=TrendSchema,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 150, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=len(user_prompt) // 4,
                    output_tokens=150,
                    cost_usd=cost,
                )

                if not result.is_market_trend:
                    return None

                all_cluster_events = [e for evts in events_by_company.values() for e in evts]
                return MarketTrendEvent(
                    company="market",
                    event_type="market_trend",  # type: ignore
                    timestamp=datetime.now(tz=timezone.utc).isoformat(),
                    summary=f"{result.trend_name}: {result.what_this_means}",
                    source_urls=[
                        url
                        for e in all_cluster_events
                        for url in e.get("source_urls", [])
                    ][:5],
                    confidence_score=result.confidence,
                    stakeholder_tags=["ceo", "marketing", "product"],
                    trend_name=result.trend_name,
                    companies_involved=result.companies_involved,
                    data_freshness_threshold_days=30,
                )
            except Exception as exc:
                log.error(
                    "trend_synthesis_failed",
                    agent=self.name,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                return None

    async def _store_trend(self, trend: MarketTrendEvent) -> None:
        doc = trend.model_dump()
        await self._event_store.insert_event(doc)
        log.info(
            "trend_stored",
            agent=self.name,
            trend_name=trend.trend_name,
            companies=trend.companies_involved,
            confidence=trend.confidence_score,
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
    matrix = np.array(embeddings, dtype=np.float32)
    matrix = normalize(matrix, norm="l2")
    db = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine")
    labels = db.fit_predict(matrix)
    return labels.tolist()
