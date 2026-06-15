"""Hiring Signal Agent — weekly job posting analysis → WeakSignalPrediction.

Schedule: Sunday 3AM.
Input: Job postings collected from Indeed/Glassdoor/LinkedIn (last 60 days).
Output: WeakSignalPrediction per company (if hiring anomaly detected).

Algorithm:
1. Count role categories per company over last 60 days.
2. Compare against 180-day baseline. Flag where count > 2× baseline (z-score ≥ 2.0).
3. LLM prompt: what product/GTM direction does this hiring pattern predict?
4. Output: WeakSignalPrediction with time horizon and confidence.

Role categories defined in config/feature_taxonomy.yaml (hiring taxonomy).
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import instructor
from groq import Groq
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.narrative import HIRING_SIGNAL_SYSTEM, build_hiring_signal_prompt
from schemas.events import HiringSignalEvent, WeakSignalPrediction
from storage.event_store import EventStore
from tools.errors import AgentError, ErrorCode

log = get_logger("hiring_signal_agent")

# Role taxonomy
HIRING_CATEGORIES = [
    "ai_ml_engineering",
    "enterprise_sales",
    "security_compliance",
    "developer_relations",
    "data_engineering",
    "integrations_partnerships",
    "customer_success_enterprise",
    "product_management",
]

_ANOMALY_Z_SCORE_THRESHOLD = 2.0
_LOOKBACK_DAYS = 60
_BASELINE_DAYS = 180
_MIN_HIRES_FOR_SIGNAL = 3


class HiringSignalAgent(BaseAgent):
    """Analyses job posting patterns to produce WeakSignalPredictions.

    Runs weekly (Sunday 3AM). Reads HiringSignalEvents from event store,
    computes category counts, detects anomalies vs. 180-day baseline,
    generates predictions with Groq.
    """

    name = "hiring_signal_agent"
    description = (
        "Weekly job posting analysis: counts role categories vs. 180-day baseline, "
        "detects anomalies, generates WeakSignalPredictions with time horizons. "
        "Schedule: Sunday 3AM. Model: Groq (classification task)."
    )

    def __init__(
        self,
        event_store: EventStore,
        model_config: dict,
        cost_config: dict,
        anomaly_z_threshold: float = _ANOMALY_Z_SCORE_THRESHOLD,
    ) -> None:
        self._event_store = event_store
        self._model_config = model_config
        self._cost_config = cost_config
        self._anomaly_z_threshold = anomaly_z_threshold
        self._groq_client = instructor.from_groq(Groq())

    async def run(
        self,
        companies: list[str],
        run_id: str | None = None,
    ) -> list[WeakSignalPrediction]:
        """Generate weak signal predictions for all given companies."""
        run_id = run_id or str(uuid.uuid4())
        predictions: list[WeakSignalPrediction] = []

        for company in companies:
            try:
                prediction = await self._analyse_company(company, run_id)
                if prediction:
                    await self._store_prediction(prediction)
                    predictions.append(prediction)
            except AgentError as exc:
                log.error(
                    "hiring_signal_failed",
                    agent=self.name,
                    competitor=company,
                    error_code=exc.code,
                    error=exc.message,
                )
            except Exception as exc:
                log.error(
                    "hiring_signal_unexpected_error",
                    agent=self.name,
                    competitor=company,
                    error_code=ErrorCode.AGENT_STATE_INVALID,
                    error=str(exc),
                )

        log.info(
            "hiring_signal_completed",
            agent=self.name,
            action="run",
            run_id=run_id,
            companies=len(companies),
            predictions=len(predictions),
            status="completed",
        )
        return predictions

    async def _analyse_company(
        self, company: str, run_id: str
    ) -> WeakSignalPrediction | None:
        now = datetime.now(tz=timezone.utc)

        # Fetch hiring events from last 60 days
        recent_hiring = await self._event_store.get_recent_events(
            company=company,
            days=_LOOKBACK_DAYS,
            event_types=["hiring_trend", "hiring_signal"],
            min_confidence=0.6,
            limit=200,
        )

        # Fetch 180-day baseline
        baseline_hiring = await self._event_store.get_recent_events(
            company=company,
            days=_BASELINE_DAYS,
            event_types=["hiring_trend", "hiring_signal"],
            min_confidence=0.6,
            limit=500,
        )

        if not recent_hiring:
            return None

        # Count role categories in recent window
        recent_counts = _count_categories(recent_hiring)
        baseline_counts = _count_categories(baseline_hiring)

        # Compute z-scores to detect anomalies
        anomalies = _detect_anomalies(
            recent_counts=recent_counts,
            baseline_counts=baseline_counts,
            lookback_days=_LOOKBACK_DAYS,
            baseline_days=_BASELINE_DAYS,
            threshold=self._anomaly_z_threshold,
        )

        # Require minimum hires total to avoid noise
        total_recent = sum(recent_counts.values())
        if total_recent < _MIN_HIRES_FOR_SIGNAL:
            log.info(
                "insufficient_hiring_data",
                agent=self.name,
                competitor=company,
                total_hires=total_recent,
            )
            return None

        # Generate prediction
        prediction_data = await self._generate_prediction(
            company=company,
            role_counts=recent_counts,
            baseline_counts=baseline_counts,
            anomalies=anomalies,
        )

        if not prediction_data.get("has_signal"):
            return None

        # Collect supporting event IDs
        supporting_ids = [
            str(e.get("_id", ""))
            for e in recent_hiring
            if any(
                cat in (e.get("role_categories") or [e.get("role_category", "")])
                for cat in prediction_data.get("supporting_categories", [])
            )
        ][:10]

        return WeakSignalPrediction(
            company=company,
            predicted_direction=prediction_data["predicted_direction"],
            time_horizon_months=prediction_data["time_horizon_months"],
            supporting_hiring_event_ids=supporting_ids or [str(e.get("_id", "")) for e in recent_hiring[:3]],
            confidence=prediction_data["confidence"],
            generated_date=now.isoformat(),
            stakeholder_tags=["ceo", "product", "marketing"],
        )

    async def _generate_prediction(
        self,
        company: str,
        role_counts: dict[str, int],
        baseline_counts: dict[str, int],
        anomalies: list[dict],
    ) -> dict[str, Any]:
        model = self._model_config.get("extraction", "llama-3.3-70b-versatile")
        user_prompt = build_hiring_signal_prompt(
            company=company,
            role_counts=role_counts,
            baseline_counts=baseline_counts,
            anomalies=anomalies,
            lookback_days=_LOOKBACK_DAYS,
        )

        class PredictionSchema(BaseModel):
            has_signal: bool
            predicted_direction: str
            time_horizon_months: int
            confidence: float
            supporting_categories: list[str]
            reasoning: str

        async with trace_span(self.name, "generate_prediction") as span:
            try:
                result = self._groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": HIRING_SIGNAL_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=PredictionSchema,
                    max_retries=2,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 256, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=len(user_prompt) // 4,
                    output_tokens=256,
                    cost_usd=cost,
                )
                return result.model_dump()
            except Exception as exc:
                log.error(
                    "hiring_prediction_failed",
                    agent=self.name,
                    competitor=company,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                return {"has_signal": False}

    async def _store_prediction(self, prediction: WeakSignalPrediction) -> None:
        doc = prediction.model_dump()
        doc["event_type"] = "weak_signal_prediction"
        doc["timestamp"] = prediction.generated_date
        doc["source_urls"] = []
        doc["confidence_score"] = prediction.confidence
        doc["summary"] = (
            f"{prediction.company}: predicted {prediction.predicted_direction} "
            f"in {prediction.time_horizon_months} months"
        )
        await self._event_store.insert_event(doc)
        log.info(
            "prediction_stored",
            agent=self.name,
            competitor=prediction.company,
            direction=prediction.predicted_direction,
            months=prediction.time_horizon_months,
            confidence=prediction.confidence,
            status="ok",
        )

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if store_ok else "degraded",
            "dependencies": {"event_store": "ok" if store_ok else "failed"},
        }


def _count_categories(events: list[dict]) -> dict[str, int]:
    """Count hiring events by role_category or role_categories list."""
    counts: dict[str, int] = {cat: 0 for cat in HIRING_CATEGORIES}
    for event in events:
        # HiringTrendEvent has role_categories (list)
        cats = event.get("role_categories", [])
        if isinstance(cats, list):
            for cat in cats:
                if cat in counts:
                    counts[cat] += 1
        # HiringSignalEvent has role_category (str)
        cat = event.get("role_category", "")
        if cat and cat in counts:
            counts[cat] += 1
    return counts


def _detect_anomalies(
    recent_counts: dict[str, int],
    baseline_counts: dict[str, int],
    lookback_days: int,
    baseline_days: int,
    threshold: float,
) -> list[dict]:
    """Detect categories where recent hiring significantly exceeds baseline rate."""
    baseline_daily_rate = {
        cat: count / baseline_days
        for cat, count in baseline_counts.items()
    }
    expected_in_window = {
        cat: rate * lookback_days
        for cat, rate in baseline_daily_rate.items()
    }

    anomalies = []
    for cat in HIRING_CATEGORIES:
        current = recent_counts.get(cat, 0)
        expected = expected_in_window.get(cat, 0)

        if expected < 0.5:
            # Avoid division by near-zero: if baseline is very low and we have hires, flag it
            if current >= 3:
                anomalies.append({
                    "category": cat,
                    "current": current,
                    "baseline": expected,
                    "z_score": 3.0,
                })
            continue

        # Poisson approximation: variance ≈ expected
        std_dev = math.sqrt(expected)
        z_score = (current - expected) / std_dev if std_dev > 0 else 0.0

        if z_score >= threshold:
            anomalies.append({
                "category": cat,
                "current": current,
                "baseline": round(expected, 1),
                "z_score": round(z_score, 2),
            })

    return sorted(anomalies, key=lambda x: x["z_score"], reverse=True)
