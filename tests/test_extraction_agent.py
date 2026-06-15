"""Tests for ExtractionAgent — schema validation, quarantine, and unchanged-content skipping.

All LLM calls and storage writes are mocked. Tests validate that:
- Typed Pydantic events are produced (not raw dicts)
- Low-confidence results are quarantined, not stored in the event store
- Unchanged content (is_changed=False) is skipped without any LLM call
- Irrelevant content (pre-filter returns relevant=False) skips extraction
- All eight event type schemas construct correctly
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from schemas.events import EventType, FeatureLaunchEvent
from schemas.state import CrawlResult


def _make_crawl_result(
    url: str = "https://acme.example.com/blog",
    is_changed: bool = True,
) -> CrawlResult:
    return CrawlResult(
        url=url,
        content="# Acme launches enterprise SSO\n\nAcme Corp today announced SSO integration.",
        crawl_timestamp="2026-06-14T09:00:00+00:00",
        content_hash="abc123def456",
        status_code=200,
        is_changed=is_changed,
    )


def _make_feature_launch_pydantic(confidence: float = 0.9) -> FeatureLaunchEvent:
    return FeatureLaunchEvent(
        company="Acme",
        event_type=EventType.FEATURE_LAUNCH,
        timestamp="2026-06-14",
        summary="Acme launched enterprise SSO for all Pro plans.",
        source_urls=["https://acme.example.com/blog"],
        confidence_score=confidence,
        feature_name="Enterprise SSO",
        stakeholder_tags=["sales", "product"],
    )


# ── Schema contract tests (no LLM, no I/O) ───────────────────────────────────

class TestEventSchemaContracts:
    def test_feature_launch_validates_correctly(self):
        event = _make_feature_launch_pydantic()
        assert event.company == "Acme"
        assert event.event_type == EventType.FEATURE_LAUNCH
        assert event.confidence_score == 0.9
        assert event.feature_name == "Enterprise SSO"
        assert event.schema_version == "1.0"

    def test_missing_required_feature_name_fails(self):
        with pytest.raises(ValidationError):
            FeatureLaunchEvent(
                company="Acme",
                event_type=EventType.FEATURE_LAUNCH,
                timestamp="2026-06-14",
                summary="Acme launched something.",
                source_urls=["https://acme.example.com"],
                confidence_score=0.8,
                # feature_name missing
            )

    def test_all_eight_event_types_construct(self):
        from schemas.events import (
            AcquisitionEvent,
            FundingEvent,
            HiringTrendEvent,
            MarketTrendEvent,
            PartnershipEvent,
            PricingChangeEvent,
            ProductUpdateEvent,
        )
        base = {
            "company": "Acme",
            "timestamp": "2026-06-14",
            "summary": "Something significant happened at Acme Corp today.",
            "source_urls": ["https://acme.example.com"],
            "confidence_score": 0.8,
        }
        PricingChangeEvent(**base, change_direction="increase")
        ProductUpdateEvent(**base, update_category="bug_fix")
        FundingEvent(**base)
        AcquisitionEvent(**base)
        PartnershipEvent(**base)
        HiringTrendEvent(**base)
        MarketTrendEvent(**base, trend_name="AI push")


# ── Extraction pipeline flow tests (mocked LLM + storage) ────────────────────

def _make_agent(fake_model_config, fake_cost_config):
    from agents.extraction_agent import ExtractionAgent

    event_store = MagicMock()
    event_store.insert_event = AsyncMock(return_value="event_id_123")
    event_store.insert_quarantined_event = AsyncMock()
    event_store.add_source_to_event = AsyncMock()

    vector_store = MagicMock()
    vector_store.find_similar = AsyncMock(return_value=[])
    vector_store.upsert_embedding = AsyncMock()

    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()

    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 1536)

    # LLMAdapter is now injected — mock it so no real API keys or network calls needed.
    llm_adapter = MagicMock()
    llm_adapter.get_instructor_client = MagicMock(return_value=MagicMock())
    llm_adapter.get_chat_client = MagicMock(return_value=MagicMock())

    agent = ExtractionAgent(
        event_store=event_store,
        vector_store=vector_store,
        cache=cache,
        embedder=embedder,
        model_config=fake_model_config,
        cost_config=fake_cost_config,
        llm_adapter=llm_adapter,
    )
    return agent, event_store, vector_store


class TestExtractionPipelineFlow:
    @pytest.mark.asyncio
    async def test_unchanged_content_returns_early_without_llm(
        self, fake_model_config, fake_cost_config
    ):
        agent, event_store, _ = _make_agent(fake_model_config, fake_cost_config)

        crawl = _make_crawl_result(is_changed=False)
        result = await agent.run(crawl_result=crawl, company="Acme", run_id="test")

        assert result.skipped_count == 1
        event_store.insert_event.assert_not_called()
        event_store.insert_quarantined_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_irrelevant_prefilter_skips_extraction(
        self, fake_model_config, fake_cost_config
    ):
        agent, event_store, _ = _make_agent(fake_model_config, fake_cost_config)

        pre_filter_result = {
            "relevant": False,
            "likely_event_type": None,
            "reason": "routine changelog, no market-relevant event",
            "cost_usd": 0.0001,
        }

        with patch.object(agent, "_pre_filter", new=AsyncMock(return_value=pre_filter_result)):
            crawl = _make_crawl_result()
            result = await agent.run(crawl_result=crawl, company="Acme", run_id="test")

        assert result.skipped_count == 1
        event_store.insert_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_quarantine(
        self, fake_model_config, fake_cost_config
    ):
        agent, event_store, _ = _make_agent(fake_model_config, fake_cost_config)

        low_conf_event = _make_feature_launch_pydantic(confidence=0.55)

        pre_filter_result = {
            "relevant": True,
            "likely_event_type": "feature_launch",
            "reason": "feature announcement",
            "cost_usd": 0.0001,
        }
        extraction_result = {
            "event": low_conf_event,
            "error": None,
            "error_code": None,
            "cost_usd": 0.003,
        }
        judge_result = {
            "recommended_action": "quarantine",
            "issues": ["confidence too low", "summary vague"],
            "cost_usd": 0.005,
        }

        with patch.object(agent, "_pre_filter", new=AsyncMock(return_value=pre_filter_result)), \
             patch.object(agent, "_extract_event", new=AsyncMock(return_value=extraction_result)), \
             patch.object(agent, "_judge_event", new=AsyncMock(return_value=judge_result)):

            crawl = _make_crawl_result()
            result = await agent.run(crawl_result=crawl, company="Acme", run_id="test")

        assert result.quarantined_count == 1
        event_store.insert_quarantined_event.assert_called_once()
        event_store.insert_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_confidence_event_is_stored(
        self, fake_model_config, fake_cost_config
    ):
        agent, event_store, _ = _make_agent(fake_model_config, fake_cost_config)

        high_conf_event = _make_feature_launch_pydantic(confidence=0.92)

        pre_filter_result = {
            "relevant": True,
            "likely_event_type": "feature_launch",
            "reason": "feature announcement",
            "cost_usd": 0.0001,
        }
        extraction_result = {
            "event": high_conf_event,
            "error": None,
            "error_code": None,
            "cost_usd": 0.003,
        }

        with patch.object(agent, "_pre_filter", new=AsyncMock(return_value=pre_filter_result)), \
             patch.object(agent, "_extract_event", new=AsyncMock(return_value=extraction_result)), \
             patch.object(agent, "_deduplicate", new=AsyncMock(return_value=(False, None))):

            crawl = _make_crawl_result()
            result = await agent.run(crawl_result=crawl, company="Acme", run_id="test")

        event_store.insert_event.assert_called_once()
        assert result.quarantined_count == 0
        assert len(result.events_extracted) == 1
