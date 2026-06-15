from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    QUARANTINED = "QUARANTINED"


class CrawlResult(BaseModel):
    url: str
    content: str = Field(..., description="Markdown content from crawler")
    crawl_timestamp: str = Field(..., description="ISO 8601 datetime when crawl completed")
    content_hash: str = Field(..., description="SHA-256 hash of meaningful content sections")
    etag: str | None = Field(default=None)
    last_modified: str | None = Field(default=None)
    status_code: int
    is_changed: bool = Field(
        ..., description="False when content hash matches cached hash — skip extraction"
    )
    company: str | None = Field(
        default=None,
        description="Competitor name — set by ResearchAgent for Tavily results whose URL "
                    "cannot be matched back to a source registry entry",
    )


class ExtractionResult(BaseModel):
    source_url: str
    crawl_timestamp: str
    events_extracted: list[dict[str, Any]] = Field(default_factory=list)
    quarantined_count: int = Field(default=0)
    skipped_count: int = Field(default=0)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    llm_cost_usd: float = Field(default=0.0)


class PipelineState(BaseModel):
    run_id: str = Field(..., description="Unique identifier for this pipeline run")
    started_at: str = Field(..., description="ISO 8601 datetime")
    status: AgentStatus = Field(default=AgentStatus.PENDING)
    competitor: str = Field(..., description="Canonical competitor name being processed")
    source_url: str
    current_step: str = Field(default="init")
    crawl_result: CrawlResult | None = Field(default=None)
    extraction_result: ExtractionResult | None = Field(default=None)
    total_cost_usd: float = Field(default=0.0)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    retry_count: int = Field(default=0)


class CoverageResult(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0, description="Semantic relevance of KB content")
    is_stale: bool = Field(default=False)
    reason: str = Field(
        ...,
        description="'sufficient', 'insufficient', 'stale', 'no_results'",
    )
    requires_live_search: bool = Field(default=False)


class ResponseConfidence(BaseModel):
    overall: float = Field(..., ge=0.0, le=1.0)
    components: dict[str, float] = Field(
        ...,
        description=(
            "Keys: source_quality, coverage_sufficiency, data_freshness, corroboration. "
            "Each 0.0–1.0."
        ),
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Human-readable caveats e.g. 'pricing data is 8 days old'",
    )


class QuarantinedEvent(BaseModel):
    quarantine_id: str
    source_url: str
    raw_content_excerpt: str = Field(..., max_length=2000)
    extracted_event: dict[str, Any] = Field(..., description="Raw extracted dict before validation")
    confidence_score: float
    error_code: str
    error_details: str
    created_at: str = Field(..., description="ISO 8601 datetime")
    status: str = Field(default="pending", description="'pending', 'approved', 'corrected', 'rejected'")
    human_reviewed: bool = Field(default=False)
    human_corrected_fields: dict[str, Any] = Field(default_factory=dict)
