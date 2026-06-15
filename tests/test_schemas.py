"""Schema contract tests — validate that Pydantic models enforce their invariants.

Tests validate against schemas, not strings (per CLAUDE.md).
No LLM calls, no network calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.events import (
    AcquisitionEvent,
    BaseEvent,
    CustomerSentimentEvent,
    EnrichedFact,
    EventType,
    FeatureLaunchEvent,
    FundingEvent,
    HiringSignalEvent,
    MarketTrendEvent,
    NarrativeEvent,
    PartnershipEvent,
    PricingChangeEvent,
    ProductUpdateEvent,
    ThreatScore,
    WeakSignalPrediction,
)
from schemas.state import CoverageResult, CrawlResult, ExtractionResult, PipelineState


# ── BaseEvent constraints ─────────────────────────────────────────────────────

class TestBaseEvent:
    def test_valid_feature_launch(self):
        event = FeatureLaunchEvent(
            company="Acme Corp",
            event_type=EventType.FEATURE_LAUNCH,
            timestamp="2026-06-14T10:00:00+00:00",
            summary="Acme launched AI-assisted onboarding for enterprise customers.",
            source_urls=["https://acme.example.com/blog/ai-onboarding"],
            confidence_score=0.92,
            feature_name="AI Onboarding",
        )
        assert event.schema_version == "1.0"
        assert event.company == "Acme Corp"
        assert event.confidence_score == 0.92
        assert event.data_freshness_threshold_days == 14

    def test_confidence_score_must_be_between_0_and_1(self):
        with pytest.raises(ValidationError) as exc_info:
            FeatureLaunchEvent(
                company="Acme",
                event_type=EventType.FEATURE_LAUNCH,
                timestamp="2026-06-14",
                summary="Launched a feature.",
                source_urls=["https://acme.example.com"],
                confidence_score=1.5,
                feature_name="Feature X",
            )
        errors = exc_info.value.errors()
        fields = [e["loc"][-1] for e in errors]
        assert "confidence_score" in fields

    def test_confidence_score_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            FeatureLaunchEvent(
                company="Acme",
                event_type=EventType.FEATURE_LAUNCH,
                timestamp="2026-06-14",
                summary="Launched a feature.",
                source_urls=["https://acme.example.com"],
                confidence_score=-0.1,
                feature_name="Feature X",
            )

    def test_source_urls_must_not_be_empty_list(self):
        with pytest.raises(ValidationError):
            FeatureLaunchEvent(
                company="Acme",
                event_type=EventType.FEATURE_LAUNCH,
                timestamp="2026-06-14",
                summary="Launched a feature.",
                source_urls=[],
                confidence_score=0.9,
                feature_name="Feature X",
            )

    def test_source_urls_must_not_contain_blank_strings(self):
        with pytest.raises(ValidationError):
            FeatureLaunchEvent(
                company="Acme",
                event_type=EventType.FEATURE_LAUNCH,
                timestamp="2026-06-14",
                summary="Launched a feature.",
                source_urls=["https://acme.example.com", ""],
                confidence_score=0.9,
                feature_name="Feature X",
            )

    def test_summary_minimum_length_enforced(self):
        with pytest.raises(ValidationError):
            FeatureLaunchEvent(
                company="Acme",
                event_type=EventType.FEATURE_LAUNCH,
                timestamp="2026-06-14",
                summary="short",
                source_urls=["https://acme.example.com"],
                confidence_score=0.9,
                feature_name="Feature X",
            )

    def test_stakeholder_tags_defaults_to_empty_list(self):
        event = FeatureLaunchEvent(
            company="Acme",
            event_type=EventType.FEATURE_LAUNCH,
            timestamp="2026-06-14",
            summary="Acme launched enterprise SSO integration.",
            source_urls=["https://acme.example.com"],
            confidence_score=0.8,
            feature_name="SSO Integration",
        )
        assert event.stakeholder_tags == []


# ── Event-type-specific constraints ──────────────────────────────────────────

class TestPricingChangeEvent:
    def test_valid(self):
        event = PricingChangeEvent(
            company="Acme",
            timestamp="2026-06-01",
            summary="Acme raised its Pro tier price by 20% effective July 2026.",
            source_urls=["https://acme.example.com/pricing"],
            confidence_score=0.85,
            change_direction="increase",
            affected_tiers=["Pro"],
        )
        assert event.data_freshness_threshold_days == 7
        assert event.change_direction == "increase"

    def test_missing_change_direction_fails(self):
        with pytest.raises(ValidationError):
            PricingChangeEvent(
                company="Acme",
                timestamp="2026-06-01",
                summary="Acme changed its pricing structure significantly.",
                source_urls=["https://acme.example.com/pricing"],
                confidence_score=0.85,
            )


class TestFundingEvent:
    def test_freshness_threshold_is_90_days(self):
        event = FundingEvent(
            company="Acme",
            timestamp="2026-05-01",
            summary="Acme Corp raised $100M Series C led by Sequoia Capital.",
            source_urls=["https://techcrunch.example.com/acme-series-c"],
            confidence_score=0.95,
        )
        assert event.data_freshness_threshold_days == 90

    def test_optional_fields_default_to_none(self):
        event = FundingEvent(
            company="Acme",
            timestamp="2026-05-01",
            summary="Acme Corp raised a significant Series C round.",
            source_urls=["https://techcrunch.example.com/acme"],
            confidence_score=0.8,
        )
        assert event.round_type is None
        assert event.amount_usd is None
        assert event.lead_investor is None


class TestNarrativeEvent:
    def test_requires_at_least_3_constituent_events(self):
        with pytest.raises(ValidationError):
            NarrativeEvent(
                company="Acme",
                narrative_title="Enterprise Pivot",
                narrative_summary="A" * 50,
                constituent_event_ids=["evt1", "evt2"],
                time_window_days=90,
                confidence=0.8,
                generated_date="2026-06-14",
            )

    def test_requires_summary_of_50_chars(self):
        with pytest.raises(ValidationError):
            NarrativeEvent(
                company="Acme",
                narrative_title="Enterprise Pivot",
                narrative_summary="Too short.",
                constituent_event_ids=["evt1", "evt2", "evt3"],
                time_window_days=90,
                confidence=0.8,
                generated_date="2026-06-14",
            )

    def test_valid_with_three_events(self):
        event = NarrativeEvent(
            company="Acme",
            narrative_title="Enterprise Pivot",
            narrative_summary="Acme is systematically repositioning from SMB to enterprise "
                              "through three key moves: SSO launch, dedicated CSM team, and "
                              "enterprise pricing tier.",
            constituent_event_ids=["evt1", "evt2", "evt3"],
            time_window_days=90,
            confidence=0.82,
            generated_date="2026-06-14",
        )
        assert event.schema_version == "1.0"


class TestThreatScore:
    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            ThreatScore(
                company="Acme",
                score=105.0,
                tier="HIGH",
                trend="increasing",
                score_components={"velocity": 40.0, "type_weight": 35.0, "recency": 25.0},
                narrative="Acme is rated HIGH.",
                generated_date="2026-06-14",
            )

    def test_valid(self):
        ts = ThreatScore(
            company="Acme",
            score=78.5,
            tier="HIGH",
            trend="increasing",
            score_components={"velocity": 38.0, "type_weight": 25.5, "recency": 15.0},
            narrative="Acme moved aggressively on pricing and features this week.",
            contributing_event_ids=["evt1", "evt2"],
            generated_date="2026-06-14T08:00:00+00:00",
        )
        assert ts.tier == "HIGH"
        assert ts.schema_version == "1.0"


class TestCustomerSentimentEvent:
    def test_sentiment_score_bounds(self):
        with pytest.raises(ValidationError):
            CustomerSentimentEvent(
                company="Acme",
                source_platform="g2",
                aspect="pricing_value",
                sentiment="negative",
                sentiment_score=1.5,
                review_count=10,
                date_range="2026-05-01 to 2026-06-01",
                confidence_score=0.8,
            )

    def test_review_count_minimum_is_1(self):
        with pytest.raises(ValidationError):
            CustomerSentimentEvent(
                company="Acme",
                source_platform="g2",
                aspect="pricing_value",
                sentiment="negative",
                sentiment_score=-0.5,
                review_count=0,
                date_range="2026-05-01 to 2026-06-01",
                confidence_score=0.8,
            )


class TestWeakSignalPrediction:
    def test_time_horizon_max_24_months(self):
        with pytest.raises(ValidationError):
            WeakSignalPrediction(
                company="Acme",
                predicted_direction="enterprise_launch",
                time_horizon_months=36,
                supporting_hiring_event_ids=["h1"],
                confidence=0.7,
                generated_date="2026-06-14",
            )

    def test_requires_at_least_one_hiring_event_id(self):
        with pytest.raises(ValidationError):
            WeakSignalPrediction(
                company="Acme",
                predicted_direction="enterprise_launch",
                time_horizon_months=6,
                supporting_hiring_event_ids=[],
                confidence=0.7,
                generated_date="2026-06-14",
            )


# ── State schemas ─────────────────────────────────────────────────────────────

class TestCrawlResult:
    def test_valid(self):
        result = CrawlResult(
            url="https://acme.example.com/blog",
            content="# Acme launches AI onboarding\n\nContent here...",
            crawl_timestamp="2026-06-14T09:00:00+00:00",
            content_hash="abc123",
            status_code=200,
            is_changed=True,
        )
        assert result.is_changed is True
        assert result.etag is None


class TestCoverageResult:
    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            CoverageResult(score=1.5, reason="sufficient")

    def test_valid_defaults(self):
        result = CoverageResult(score=0.85, reason="sufficient")
        assert result.is_stale is False
