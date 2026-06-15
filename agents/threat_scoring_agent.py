"""Threat Scoring Agent — computes per-competitor threat scores every Sunday 7AM.

Input: All events for each tracked company from the last 90 days.
Output: ThreatScore document per company, stored in event store.

Scoring formula (0–100):
  velocity_score = z-score(events_this_week / 90day_baseline) × velocity_weight
  type_score     = weighted_sum(event_type_multipliers) × type_weight
  recency_score  = sum(weight × exp(-λ × days_ago)) × recency_weight
  total          = velocity + type + recency (normalised 0–100)

Tier thresholds (from config/thresholds.yaml):
  HIGH   > 70
  MEDIUM 40–70
  LOW    < 40

Trend: delta vs. prior week's score (+10 → increasing, -10 → decreasing, else stable)
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.intelligence import THREAT_NARRATIVE_SYSTEM, build_threat_narrative_prompt
from schemas.events import ThreatScore
from storage.event_store import EventStore
from tools.errors import AgentError, ErrorCode
from tools.llm_adapter import LLMAdapter

log = get_logger("threat_scoring_agent")

# Event type weights for type_score component
_EVENT_TYPE_MULTIPLIERS: dict[str, float] = {
    "pricing_change": 3.0,
    "feature_launch": 2.0,
    "acquisition": 3.0,
    "funding_event": 2.5,
    "partnership": 1.5,
    "product_update": 1.0,
    "hiring_trend": 1.0,
    "market_trend": 0.5,
    "customer_sentiment": 0.5,
}

# Exponential decay constant for recency score (half-life ≈ 14 days)
_RECENCY_LAMBDA = 0.05

_TIER_HIGH = 70.0
_TIER_MEDIUM = 40.0


class ThreatScoringInput:
    """Input to the Threat Scoring Agent."""

    def __init__(self, companies: list[str], lookback_days: int = 90) -> None:
        self.companies = companies
        self.lookback_days = lookback_days


class ThreatScoringAgent(BaseAgent):
    """Computes threat scores for all tracked companies.

    Sunday 7AM run. Reads from event store, computes scores, writes ThreatScore
    documents back to the event store. Does not serve queries.
    """

    name = "threat_scoring_agent"
    description = (
        "Computes a 0–100 threat score per tracked competitor using velocity, "
        "event-type weights, and recency decay. Runs Sunday 7AM. "
        "Output: ThreatScore documents stored in event store."
    )

    def __init__(
        self,
        event_store: EventStore,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
        velocity_weight: float = 40.0,
        type_weight: float = 35.0,
        recency_weight: float = 25.0,
        high_tier_above: float = _TIER_HIGH,
        medium_tier_above: float = _TIER_MEDIUM,
    ) -> None:
        self._event_store = event_store
        self._model_config = model_config
        self._cost_config = cost_config
        self._velocity_weight = velocity_weight
        self._type_weight = type_weight
        self._recency_weight = recency_weight
        self._high_tier_above = high_tier_above
        self._medium_tier_above = medium_tier_above
        self._llm_adapter = llm_adapter

    async def run(
        self,
        companies: list[str],
        lookback_days: int = 90,
        run_id: str | None = None,
    ) -> list[ThreatScore]:
        """Compute and store threat scores for all given companies."""
        run_id = run_id or str(uuid.uuid4())
        results: list[ThreatScore] = []

        for company in companies:
            try:
                score = await self._score_company(company, lookback_days, run_id)
                if score:
                    await self._store_score(score)
                    results.append(score)
            except AgentError as exc:
                log.error(
                    "threat_score_failed",
                    agent=self.name,
                    action="run",
                    competitor=company,
                    error_code=exc.code,
                    error=exc.message,
                    status="failed",
                )
            except Exception as exc:
                log.error(
                    "threat_score_unexpected_error",
                    agent=self.name,
                    action="run",
                    competitor=company,
                    error_code=ErrorCode.AGENT_STATE_INVALID,
                    error=str(exc),
                    status="failed",
                )

        log.info(
            "threat_scoring_completed",
            agent=self.name,
            action="run",
            run_id=run_id,
            companies=len(companies),
            scores_computed=len(results),
            status="completed",
        )
        return results

    async def _score_company(
        self, company: str, lookback_days: int, _run_id: str
    ) -> ThreatScore | None:
        now = datetime.now(tz=timezone.utc)

        # Fetch events for this company across the lookback window
        events = await self._event_store.get_recent_events(
            company=company,
            days=lookback_days,
            min_confidence=0.7,
            limit=500,
        )
        if not events:
            log.info(
                "no_events_for_company",
                agent=self.name,
                action="score_company",
                competitor=company,
            )
            return None

        # Fetch prior week events for trend calculation
        prior_score_doc = await self._event_store.get_threat_score(company=company)
        prior_score = prior_score_doc.get("score", 0.0) if prior_score_doc else 0.0

        # Component 1: velocity score
        velocity_score = self._compute_velocity(events, now)

        # Component 2: type-weighted score
        type_score = self._compute_type_score(events)

        # Component 3: recency-weighted score
        recency_score = self._compute_recency_score(events, now)

        # Normalise to 0–100
        raw = velocity_score + type_score + recency_score
        final_score = max(0.0, min(100.0, raw))

        # Tier
        tier = (
            "HIGH" if final_score >= self._high_tier_above
            else "MEDIUM" if final_score >= self._medium_tier_above
            else "LOW"
        )

        # Trend
        delta = final_score - prior_score
        trend = "increasing" if delta >= 10 else "decreasing" if delta <= -10 else "stable"

        # Top contributing events (highest-weight types, most recent)
        top_events = sorted(
            events,
            key=lambda e: (
                _EVENT_TYPE_MULTIPLIERS.get(e.get("event_type", ""), 0.5),
                e.get("timestamp", ""),
            ),
            reverse=True,
        )[:5]
        contributing_ids = [str(e.get("_id", "")) for e in top_events]

        # Generate narrative with Sonnet
        narrative = await self._generate_narrative(
            company=company,
            score=final_score,
            tier=tier,
            velocity_score=velocity_score,
            type_score=type_score,
            recency_score=recency_score,
            top_events=top_events,
        )

        return ThreatScore(
            company=company,
            score=round(final_score, 1),
            tier=tier,
            trend=trend,
            score_components={
                "velocity": round(velocity_score, 2),
                "type_weight": round(type_score, 2),
                "recency": round(recency_score, 2),
            },
            narrative=narrative,
            contributing_event_ids=contributing_ids,
            generated_date=now.isoformat(),
        )

    def _compute_velocity(self, events: list[dict], now: datetime) -> float:
        """Z-score based velocity: events this week vs. 90-day weekly baseline."""
        if not events:
            return 0.0
        weekly_counts: list[float] = []

        # Build 12 weekly buckets from the 90-day window
        for week in range(12):
            week_start = (now - timedelta(days=(week + 1) * 7)).isoformat()
            week_end = (now - timedelta(days=week * 7)).isoformat()
            count = sum(
                1 for e in events
                if week_start <= e.get("timestamp", "") < week_end
            )
            weekly_counts.append(float(count))

        if not weekly_counts:
            return 0.0

        this_week = weekly_counts[0]
        baseline_weeks = weekly_counts[1:]  # exclude current week
        if not baseline_weeks:
            return min(this_week * 5, self._velocity_weight)

        mean = sum(baseline_weeks) / len(baseline_weeks)
        variance = sum((x - mean) ** 2 for x in baseline_weeks) / len(baseline_weeks)
        std_dev = math.sqrt(variance) if variance > 0 else 1.0

        z_score = (this_week - mean) / std_dev
        # Clamp to range, then scale to velocity_weight
        z_clamped = max(-3.0, min(3.0, z_score))
        normalised = (z_clamped + 3.0) / 6.0  # 0–1
        return normalised * self._velocity_weight

    def _compute_type_score(self, events: list[dict]) -> float:
        """Weighted sum of event types, normalised to type_weight."""
        raw = sum(
            _EVENT_TYPE_MULTIPLIERS.get(e.get("event_type", ""), 0.5)
            for e in events
        )
        # Cap at 20 weighted events = full type_weight
        normalised = min(raw / 20.0, 1.0)
        return normalised * self._type_weight

    def _compute_recency_score(self, events: list[dict], now: datetime) -> float:
        """Exponential decay: recent events contribute more."""
        total = 0.0
        for event in events:
            try:
                event_dt = datetime.fromisoformat(
                    event.get("timestamp", "").replace("Z", "+00:00")
                )
                days_ago = (now - event_dt).days
                weight = _EVENT_TYPE_MULTIPLIERS.get(event.get("event_type", ""), 0.5)
                total += weight * math.exp(-_RECENCY_LAMBDA * days_ago)
            except (ValueError, TypeError):
                continue

        # Cap at 30 weighted-decayed units = full recency_weight
        normalised = min(total / 30.0, 1.0)
        return normalised * self._recency_weight

    async def _generate_narrative(
        self,
        company: str,
        score: float,
        tier: str,
        velocity_score: float,
        type_score: float,
        recency_score: float,
        top_events: list[dict],
    ) -> str:
        """Call Sonnet to generate a one-sentence threat narrative."""
        model = self._model_config.get("synthesis", "claude-sonnet-4-6")
        prompt = build_threat_narrative_prompt(
            company=company,
            score=score,
            tier=tier,
            velocity_score=velocity_score,
            type_score=type_score,
            recency_score=recency_score,
            top_events=top_events,
        )

        async with trace_span(self.name, "generate_narrative") as span:
            try:
                client = self._llm_adapter.get_chat_client(model)
                response = client.chat.completions.create(
                    model=model,
                    max_tokens=200,
                    messages=[
                        {"role": "system", "content": THREAT_NARRATIVE_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                )
                narrative = response.choices[0].message.content.strip()
                usage = response.usage
                input_tokens = usage.prompt_tokens if usage else len(prompt) // 4
                output_tokens = usage.completion_tokens if usage else 50
                cost = calculate_cost(model, input_tokens, output_tokens, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
                return narrative
            except Exception as exc:
                log.error(
                    "narrative_generation_failed",
                    agent=self.name,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                return f"{company} is rated {tier} (score: {score:.0f}/100)."

    async def _store_score(self, score: ThreatScore) -> None:
        """Store threat score as an event in the event store."""
        doc = score.model_dump()
        doc["event_type"] = "threat_score"
        await self._event_store.insert_event(doc)
        log.info(
            "threat_score_stored",
            agent=self.name,
            action="store_score",
            competitor=score.company,
            score=score.score,
            tier=score.tier,
            trend=score.trend,
            status="ok",
        )

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if store_ok else "degraded",
            "dependencies": {"event_store": "ok" if store_ok else "failed"},
        }
