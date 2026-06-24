from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class SourceConfig(BaseModel):
    type: str = Field(
        ...,
        description=(
            "'rss', 'firecrawl', 'apify', 'tavily', 'edgar', "
            "'companies_house', 'patents', 'court_listener', "
            "'semantic_scholar', 'github', 'alpha_vantage', "
            "'open_secrets', 'mca21'"
        ),
    )
    url: str | None = Field(default=None)
    frequency: str = Field(default="daily", description="'30min', 'daily', 'weekly'")
    apify_actor: str | None = Field(default=None, alias="actor")
    apify_handle: str | None = Field(default=None, alias="handle")
    apify_slug: str | None = Field(default=None, alias="slug")
    apify_query: str | None = Field(default=None, alias="query")
    fallback: str | None = Field(default=None, description="Fallback strategy e.g. 'tavily'")
    max_results: int | None = Field(default=None, description="Max items to fetch from Apify actors")
    entity: str | None = Field(default=None, description="Company name for EDGAR search (overrides competitor name)")
    label: str | None = Field(default=None, description="Human-readable label for this source")
    input: dict | None = Field(default=None, description="Arbitrary input dict for Apify actors (e.g. Reddit scraper)")

    model_config = {"populate_by_name": True}


class CompetitorConfig(BaseModel):
    competitor: str = Field(..., description="Canonical name used as the primary key")
    canonical_names: list[str] = Field(
        ...,
        min_length=1,
        description="All known names/aliases for this company (for entity resolution)",
    )
    sources: list[SourceConfig]

    @field_validator("canonical_names")
    @classmethod
    def canonical_names_must_include_competitor(cls, v: list[str], info) -> list[str]:
        data = info.data
        if "competitor" in data and data["competitor"] not in v:
            v.insert(0, data["competitor"])
        return v


class ModelConfig(BaseModel):
    pre_filter: str = Field(..., description="Model for content relevance pre-filtering")
    extraction: str = Field(..., description="Model for structured event extraction")
    validation: str = Field(..., description="Model for judge pass (low-confidence events)")
    synthesis: str = Field(..., description="Model for insight generation and complex reasoning")
    conversational: str = Field(..., description="Model for user-facing conversational responses")
    embedding: str = Field(..., description="Model for generating text embeddings")


class ModelCostConfig(BaseModel):
    input_per_1k_tokens: float
    output_per_1k_tokens: float


class StakeholderProfile(BaseModel):
    role: str = Field(..., description="Identifier e.g. 'sales', 'ceo'")
    display_name: str
    cares_about: list[str]
    decision_context: str
    vocabulary_style: str
    default_stakeholder_tags: list[str] = Field(
        ..., description="Event tags that map to this role"
    )


class FreshnessThresholds(BaseModel):
    pricing: int = Field(default=7)
    revenue: int = Field(default=30)
    feature_status: int = Field(default=14)
    funding: int = Field(default=90)
    hiring_signals: int = Field(default=30)
    partnerships: int = Field(default=60)
    sentiment: int = Field(default=14)
    default: int = Field(default=14)


class ConfidenceThresholds(BaseModel):
    quarantine_below: float = Field(
        default=0.7,
        description="Events with confidence below this are quarantined for human review",
    )
    dedup_merge_above: float = Field(
        default=0.88,
        description="Cosine similarity above which two events are merged as duplicates",
    )
    coverage_sufficient_above: float = Field(
        default=0.7,
        description="Coverage score above which KB is considered sufficient for a query",
    )
    attribution_match_above: float = Field(
        default=0.75,
        description="Cosine similarity above which a claim is attributed to an event",
    )


class AppConfig(BaseSettings):
    groq_api_key: str
    openrouter_api_key: str
    # OpenAI key is only needed for embeddings; optional if using a different embedding provider
    openai_api_key: str | None = Field(default=None)
    # Deprecated: Anthropic is no longer called directly — use OpenRouter instead
    anthropic_api_key: str | None = Field(default=None)
    firecrawl_api_key: str | None = Field(default=None)
    tavily_api_key: str | None = Field(default=None)
    apify_api_key: str | None = Field(default=None)
    # Data source API keys — all free/registration-only tiers
    companies_house_api_key: str | None = Field(default=None)
    lens_api_key: str | None = Field(default=None)
    court_listener_token: str | None = Field(default=None)
    github_token: str | None = Field(default=None)
    alpha_vantage_api_key: str | None = Field(default=None)
    open_secrets_api_key: str | None = Field(default=None)
    mongodb_uri: str = Field(default="mongodb://localhost:27017")
    mongodb_db_name: str = Field(default="market_intelligence")
    supabase_url: str | None = Field(default=None)
    supabase_service_key: str | None = Field(default=None)
    redis_url: str = Field(default="redis://localhost:6379")
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = Field(default="https://cloud.langfuse.com")
    log_level: str = Field(default="INFO")
    env: str = Field(default="development")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
