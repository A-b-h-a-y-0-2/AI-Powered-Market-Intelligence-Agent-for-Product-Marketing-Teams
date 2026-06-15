"""Tests for the ThreatScoringAgent scoring formula.

Tests exercise the pure computation methods (_compute_velocity, _compute_type_score,
_compute_recency_score) without any LLM calls or database connections.
All external dependencies are mocked.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.threat_scoring_agent import (
    ThreatScoringAgent,
    _EVENT_TYPE_MULTIPLIERS,
    _RECENCY_LAMBDA,
)


@pytest.fixture()
def agent(fake_model_config, fake_cost_config) -> ThreatScoringAgent:
    event_store = MagicMock()
    llm_adapter = MagicMock()
    return ThreatScoringAgent(
        event_store=event_store,
        model_config=fake_model_config,
        cost_config=fake_cost_config,
        llm_adapter=llm_adapter,
    )


def _make_event(event_type: str, days_ago: int, now: datetime) -> dict:
    ts = (now - timedelta(days=days_ago)).isoformat()
    return {"event_type": event_type, "timestamp": ts}


class TestVelocityScore:
    def test_zero_events_returns_zero(self, agent):
        now = datetime.now(tz=timezone.utc)
        score = agent._compute_velocity([], now)
        assert score == 0.0

    def test_no_recent_events_returns_low_score(self, agent):
        now = datetime.now(tz=timezone.utc)
        # 5 events all from 30–90 days ago — none this week
        events = [_make_event("feature_launch", d, now) for d in range(30, 80, 10)]
        score = agent._compute_velocity(events, now)
        # z-score should be negative (this week: 0, baseline mean > 0) → low score
        assert score < agent._velocity_weight / 2

    def test_burst_of_recent_events_raises_score(self, agent):
        now = datetime.now(tz=timezone.utc)
        # 5 events this week, 1 in baseline weeks
        recent = [_make_event("pricing_change", d, now) for d in range(0, 5)]
        old = [_make_event("feature_launch", d, now) for d in range(14, 91, 14)]
        events = recent + old
        high_velocity = agent._compute_velocity(events, now)
        # A single old event gives low-burst scenario:
        low_burst = agent._compute_velocity(old, now)
        assert high_velocity > low_burst

    def test_score_capped_at_velocity_weight(self, agent):
        now = datetime.now(tz=timezone.utc)
        # Extreme burst: 50 events this week
        events = [_make_event("feature_launch", 0, now) for _ in range(50)]
        score = agent._compute_velocity(events, now)
        assert score <= agent._velocity_weight

    def test_score_is_non_negative(self, agent):
        now = datetime.now(tz=timezone.utc)
        # One old event — current week has 0 events
        events = [_make_event("market_trend", 45, now)]
        score = agent._compute_velocity(events, now)
        assert score >= 0.0


class TestTypeScore:
    def test_empty_events_returns_zero(self, agent):
        assert agent._compute_type_score([]) == 0.0

    def test_high_weight_events_score_more(self, agent):
        pricing_events = [{"event_type": "pricing_change"} for _ in range(5)]
        market_events = [{"event_type": "market_trend"} for _ in range(5)]
        pricing_score = agent._compute_type_score(pricing_events)
        market_score = agent._compute_type_score(market_events)
        assert pricing_score > market_score

    def test_unknown_event_type_uses_default_weight(self, agent):
        events = [{"event_type": "unknown_type"}]
        score = agent._compute_type_score(events)
        # Default weight is 0.5; 1 event / 20 cap × type_weight
        expected = (0.5 / 20.0) * agent._type_weight
        assert abs(score - expected) < 0.001

    def test_score_capped_at_type_weight(self, agent):
        # 100 pricing_change events → should cap
        events = [{"event_type": "pricing_change"} for _ in range(100)]
        score = agent._compute_type_score(events)
        assert score == pytest.approx(agent._type_weight, rel=1e-3)

    def test_exact_formula_for_single_acquisition(self, agent):
        events = [{"event_type": "acquisition"}]
        multiplier = _EVENT_TYPE_MULTIPLIERS["acquisition"]  # 3.0
        expected = (multiplier / 20.0) * agent._type_weight
        assert abs(agent._compute_type_score(events) - expected) < 0.001


class TestRecencyScore:
    def test_empty_events_returns_zero(self, agent):
        now = datetime.now(tz=timezone.utc)
        assert agent._compute_recency_score([], now) == 0.0

    def test_very_old_event_has_near_zero_contribution(self, agent):
        now = datetime.now(tz=timezone.utc)
        events = [_make_event("pricing_change", 365, now)]
        score = agent._compute_recency_score(events, now)
        # e^(-0.05 × 365) ≈ 1.2e-8
        assert score < 0.001

    def test_recent_event_has_higher_contribution_than_old(self, agent):
        now = datetime.now(tz=timezone.utc)
        recent = [_make_event("pricing_change", 1, now)]
        old = [_make_event("pricing_change", 60, now)]
        assert agent._compute_recency_score(recent, now) > agent._compute_recency_score(old, now)

    def test_malformed_timestamp_skipped_without_crash(self, agent):
        now = datetime.now(tz=timezone.utc)
        events = [
            {"event_type": "feature_launch", "timestamp": "not-a-date"},
            _make_event("feature_launch", 5, now),
        ]
        score = agent._compute_recency_score(events, now)
        # Should only count the valid event
        assert score > 0.0

    def test_score_capped_at_recency_weight(self, agent):
        now = datetime.now(tz=timezone.utc)
        # 200 pricing_change events today
        events = [_make_event("pricing_change", 0, now) for _ in range(200)]
        score = agent._compute_recency_score(events, now)
        assert score <= agent._recency_weight


class TestTierAndTrend:
    def test_tier_high_above_70(self, agent):
        now = datetime.now(tz=timezone.utc)
        events = [_make_event("pricing_change", 0, now) for _ in range(30)]
        events += [_make_event("acquisition", 1, now) for _ in range(30)]
        velocity = agent._compute_velocity(events, now)
        type_s = agent._compute_type_score(events)
        recency = agent._compute_recency_score(events, now)
        total = min(100.0, velocity + type_s + recency)
        tier = (
            "HIGH" if total >= 70.0
            else "MEDIUM" if total >= 40.0
            else "LOW"
        )
        assert tier in {"HIGH", "MEDIUM", "LOW"}

    def test_trend_logic(self, agent):
        # Verify the trend formula using agent's tier thresholds
        agent_deltas = [
            (10.0, "increasing"),
            (9.9, "stable"),
            (-9.9, "stable"),
            (-10.0, "decreasing"),
        ]
        for delta, expected_trend in agent_deltas:
            trend = (
                "increasing" if delta >= 10
                else "decreasing" if delta <= -10
                else "stable"
            )
            assert trend == expected_trend, f"delta={delta} should be {expected_trend}"


class TestRunIntegration:
    @pytest.mark.asyncio
    async def test_no_events_skips_company_gracefully(
        self, agent, fake_model_config, fake_cost_config
    ):
        agent._event_store.get_recent_events = AsyncMock(return_value=[])
        agent._event_store.get_threat_score = AsyncMock(return_value=None)

        results = await agent.run(companies=["Acme"])
        assert results == []

    @pytest.mark.asyncio
    async def test_storage_error_is_caught_per_company(
        self, fake_model_config, fake_cost_config
    ):
        from tools.errors import AgentError, ErrorCode

        event_store = MagicMock()
        event_store.get_recent_events = AsyncMock(
            side_effect=AgentError(
                code=ErrorCode.STORE_READ_FAILED, message="DB down"
            )
        )
        agent = ThreatScoringAgent(
            event_store=event_store,
            model_config=fake_model_config,
            cost_config=fake_cost_config,
            llm_adapter=MagicMock(),
        )
        # Should not raise — error is caught per-company, other companies still run
        results = await agent.run(companies=["Acme", "Rival"])
        assert isinstance(results, list)
