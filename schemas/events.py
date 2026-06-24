from __future__ import annotations

from enum import Enum
from typing import Annotated, Union

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    FEATURE_LAUNCH = "feature_launch"
    PRICING_CHANGE = "pricing_change"
    FUNDING_EVENT = "funding_event"
    ACQUISITION = "acquisition"
    PARTNERSHIP = "partnership"
    HIRING_TREND = "hiring_trend"
    PRODUCT_UPDATE = "product_update"
    MARKET_TREND = "market_trend"
    CUSTOMER_SENTIMENT = "customer_sentiment"
    HIRING_SIGNAL = "hiring_signal"
    NARRATIVE = "narrative"
    THREAT_SCORE = "threat_score"
    WEAK_SIGNAL_PREDICTION = "weak_signal_prediction"
    ENRICHED_FACT = "enriched_fact"


class BaseEvent(BaseModel):
    schema_version: str = Field(default="1.0", description="Schema version for migration tracking")
    company: str = Field(..., description="Canonical company name from source registry")
    event_type: EventType
    timestamp: str = Field(
        ...,
        description="ISO 8601 datetime. Relative dates resolved to crawl timestamp at extraction.",
    )
    summary: str = Field(..., min_length=10, description="Human-readable event summary")
    source_urls: list[str] = Field(..., min_length=1, description="One or more source URLs")
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Extraction confidence 0.0–1.0"
    )
    stakeholder_tags: list[str] = Field(
        default_factory=list,
        description="Stakeholder roles this event is relevant to. Assigned at write time.",
    )
    data_freshness_threshold_days: int = Field(
        default=14,
        description="Days after which this event is considered stale for retrieval.",
    )

    @field_validator("source_urls")
    @classmethod
    def urls_must_be_non_empty(cls, v: list[str]) -> list[str]:
        if any(not url.strip() for url in v):
            raise ValueError("source_urls must not contain empty strings")
        return v


class FeatureLaunchEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.FEATURE_LAUNCH)
    feature_name: str = Field(..., description="Name of the launched feature")
    pricing_tier_affected: str | None = Field(
        default=None, description="Which pricing tier(s) this feature applies to"
    )
    data_freshness_threshold_days: int = Field(default=14)


class PricingChangeEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.PRICING_CHANGE)
    change_direction: str = Field(
        ..., description="'increase', 'decrease', 'restructure', or 'new_tier'"
    )
    affected_tiers: list[str] = Field(
        default_factory=list, description="Which tiers changed"
    )
    old_price_signal: str | None = Field(
        default=None, description="Previous price or tier description if known"
    )
    new_price_signal: str | None = Field(
        default=None, description="New price or tier description"
    )
    data_freshness_threshold_days: int = Field(default=7)


class ProductUpdateEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.PRODUCT_UPDATE)
    update_category: str = Field(
        ..., description="'bug_fix', 'performance', 'ui_ux', 'integration', 'api', 'other'"
    )
    data_freshness_threshold_days: int = Field(default=14)


class FundingEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.FUNDING_EVENT)
    round_type: str | None = Field(
        default=None,
        description="'seed', 'series_a', 'series_b', 'series_c', 'ipo', 'debt', 'other'",
    )
    amount_usd: str | None = Field(
        default=None, description="Amount raised as string e.g. '$50M'"
    )
    lead_investor: str | None = Field(default=None)
    data_freshness_threshold_days: int = Field(default=90)


class AcquisitionEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.ACQUISITION)
    acquired_company: str | None = Field(
        default=None, description="Company that was acquired (if acquirer)"
    )
    acquirer_company: str | None = Field(
        default=None, description="Company that did the acquiring (if target)"
    )
    deal_value: str | None = Field(default=None, description="Deal value if disclosed")
    data_freshness_threshold_days: int = Field(default=90)


class PartnershipEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.PARTNERSHIP)
    partner_company: str | None = Field(default=None)
    partnership_type: str | None = Field(
        default=None,
        description="'integration', 'reseller', 'technology', 'strategic', 'other'",
    )
    data_freshness_threshold_days: int = Field(default=60)


class HiringTrendEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.HIRING_TREND)
    role_categories: list[str] = Field(
        default_factory=list,
        description="Categories of roles being hired e.g. 'ai_ml_engineering'",
    )
    hiring_velocity: str | None = Field(
        default=None, description="'accelerating', 'steady', 'slowing'"
    )
    data_freshness_threshold_days: int = Field(default=30)


class MarketTrendEvent(BaseEvent):
    event_type: EventType = Field(default=EventType.MARKET_TREND)
    trend_name: str = Field(..., description="Short label for the trend")
    companies_involved: list[str] = Field(
        default_factory=list,
        description="Companies that contribute to this trend",
    )
    data_freshness_threshold_days: int = Field(default=30)


class CustomerSentimentEvent(BaseModel):
    schema_version: str = Field(default="1.0")
    company: str
    event_type: EventType = Field(default=EventType.CUSTOMER_SENTIMENT)
    source_platform: str = Field(
        ..., description="'g2', 'capterra', 'reddit', 'glassdoor', 'twitter'"
    )
    aspect: str = Field(
        ...,
        description="Sentiment aspect from predefined taxonomy e.g. 'pricing_value'",
    )
    sentiment: str = Field(..., description="'positive', 'negative', 'mixed'")
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    representative_quotes: list[str] = Field(default_factory=list, max_length=5)
    review_count: int = Field(..., ge=1)
    date_range: str = Field(..., description="e.g. '2026-05-01 to 2026-06-15'")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    stakeholder_tags: list[str] = Field(default_factory=list)
    data_freshness_threshold_days: int = Field(default=14)

    @property
    def summary(self) -> str:
        direction = "positive" if self.sentiment_score > 0.1 else ("negative" if self.sentiment_score < -0.1 else "mixed")
        return (
            f"{direction.capitalize()} sentiment on {self.aspect.replace('_', ' ')} "
            f"from {self.review_count} reviews on {self.source_platform} "
            f"(score: {self.sentiment_score:+.2f})"
        )


class HiringSignalEvent(BaseModel):
    schema_version: str = Field(default="1.0")
    company: str
    event_type: EventType = Field(default=EventType.HIRING_SIGNAL)
    role_title: str
    role_category: str = Field(
        ...,
        description="From taxonomy: 'ai_ml_engineering', 'enterprise_sales', 'security_compliance', etc.",
    )
    seniority: str = Field(..., description="'junior', 'senior', 'director', 'vp', 'head_of', 'c_level'")
    strategic_signal: str = Field(
        ..., description="LLM-inferred forward-looking prediction based on this hire"
    )
    source_url: str
    source_platform: str = Field(
        ..., description="'indeed', 'glassdoor', 'linkedin', 'tavily_discovery'"
    )
    posting_date: str = Field(..., description="ISO 8601 date")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    stakeholder_tags: list[str] = Field(default_factory=list)
    data_freshness_threshold_days: int = Field(default=30)


class NarrativeEvent(BaseModel):
    schema_version: str = Field(default="1.0")
    company: str
    event_type: EventType = Field(default=EventType.NARRATIVE)
    narrative_title: str = Field(..., description="e.g. 'Enterprise Pivot', 'AI Feature Push'")
    narrative_summary: str = Field(..., min_length=50)
    constituent_event_ids: list[str] = Field(..., min_length=3)
    time_window_days: int
    confidence: float = Field(..., ge=0.0, le=1.0)
    generated_date: str = Field(..., description="ISO 8601 date when narrative was generated")
    stakeholder_tags: list[str] = Field(default_factory=list)


class ThreatScore(BaseModel):
    schema_version: str = Field(default="1.0")
    company: str
    event_type: EventType = Field(default=EventType.THREAT_SCORE)
    score: float = Field(..., ge=0.0, le=100.0)
    tier: str = Field(..., description="'HIGH', 'MEDIUM', or 'LOW'")
    trend: str = Field(..., description="'increasing', 'stable', or 'decreasing'")
    score_components: dict[str, float] = Field(
        ..., description="{'velocity': 40.0, 'type_weight': 35.0, 'recency': 25.0}"
    )
    narrative: str = Field(..., description="One-sentence explanation of the score")
    contributing_event_ids: list[str] = Field(default_factory=list)
    generated_date: str = Field(..., description="ISO 8601 datetime")


class WeakSignalPrediction(BaseModel):
    schema_version: str = Field(default="1.0")
    company: str
    event_type: EventType = Field(default=EventType.WEAK_SIGNAL_PREDICTION)
    predicted_direction: str = Field(
        ..., description="e.g. 'enterprise product launch', 'AI capability push'"
    )
    time_horizon_months: int = Field(..., ge=1, le=24)
    supporting_hiring_event_ids: list[str] = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    generated_date: str
    stakeholder_tags: list[str] = Field(default_factory=list)


class EnrichedFact(BaseModel):
    schema_version: str = Field(default="1.0")
    company: str
    event_type: EventType = Field(default=EventType.ENRICHED_FACT)
    fact_type: str = Field(
        ..., description="'revenue', 'employee_count', 'ceo_name', 'headquarters', etc."
    )
    value: str
    source_url: str
    discovered_date: str = Field(..., description="ISO 8601 date when fact was discovered")
    freshness_threshold_days: int = Field(
        ..., description="How many days until this fact should be re-verified"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    stakeholder_tags: list[str] = Field(default_factory=list)


AnyEvent = Annotated[
    Union[
        FeatureLaunchEvent,
        PricingChangeEvent,
        ProductUpdateEvent,
        FundingEvent,
        AcquisitionEvent,
        PartnershipEvent,
        HiringTrendEvent,
        MarketTrendEvent,
        CustomerSentimentEvent,
        HiringSignalEvent,
        NarrativeEvent,
        ThreatScore,
        WeakSignalPrediction,
        EnrichedFact,
    ],
    Field(discriminator="event_type"),
]
