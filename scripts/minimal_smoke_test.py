"""Minimal smoke test — proves extraction pipeline works end-to-end.

Uses llama-3.1-8b-instant on Groq (separate daily token counter from 70b model).
~1500 tokens total. No MongoDB/Redis/Supabase needed.

Run:
    source .venv/bin/activate && python scripts/minimal_smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from observability.logger import configure_logging
from observability.tracing import init_tracing
configure_logging("WARNING")  # suppress INFO spam
init_tracing(public_key="smoke", secret_key="smoke", host="https://cloud.langfuse.com", enabled=False)

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

if not GROQ_KEY:
    print("ERROR: GROQ_API_KEY not set in .env")
    sys.exit(1)
if not OPENROUTER_KEY:
    print("ERROR: OPENROUTER_API_KEY not set in .env")
    sys.exit(1)

# Short article — keeps token usage low
ARTICLE = """
McKinsey & Company today announced a strategic partnership with Microsoft to
co-develop AI-powered consulting tools. The deal includes a $200M joint
investment in McKinsey's QuantumBlack AI platform, with general availability
expected in Q3 2026. McKinsey clients on Enterprise plans will get early access.
"""


async def run() -> None:
    from tools.llm_adapter import LLMAdapter
    from agents.extraction_agent import ExtractionAgent
    from schemas.state import CrawlResult

    adapter = LLMAdapter(groq_api_key=GROQ_KEY, openrouter_api_key=OPENROUTER_KEY)

    # All storage mocked — no infra needed
    event_store = MagicMock()
    event_store.insert_event = AsyncMock(return_value="smoke_event_001")
    event_store.insert_quarantined_event = AsyncMock()
    event_store.add_source_to_event = AsyncMock()
    event_store.get_recent_events = AsyncMock(return_value=[])

    vector_store = MagicMock()
    vector_store.upsert_embedding = AsyncMock()

    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.0] * 1536)
    embedder.embed_batch = AsyncMock(return_value=[[0.0] * 1536])

    # Use 8b model for both passes — separate daily counter from 70b
    model_cfg = {
        "pre_filter": "llama-3.1-8b-instant",
        "extraction": "llama-3.1-8b-instant",
        "validation": "llama-3.1-8b-instant",
    }
    cost_cfg = {
        "llama-3.1-8b-instant": {"input": 0.0001, "output": 0.0001},
    }

    agent = ExtractionAgent(
        event_store=event_store,
        vector_store=vector_store,
        cache=MagicMock(),
        embedder=embedder,
        model_config=model_cfg,
        cost_config=cost_cfg,
        llm_adapter=adapter,
    )

    crawl = CrawlResult(
        url="https://mckinsey.com/press/quantumblack-microsoft-2026",
        content=ARTICLE,
        is_changed=True,
        crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
        content_hash="smoke123",
        status_code=200,
    )

    print("Running extraction agent with llama-3.1-8b-instant on Groq...")
    result = await agent.run(crawl_result=crawl, company="McKinsey & Company", run_id="smoke_min")

    print(f"\nResult:")
    print(f"  error_code:       {result.error_code or 'none'}")
    print(f"  skipped:          {result.skipped_count}")
    print(f"  events_stored:    {len(result.events_extracted)}")
    print(f"  quarantined:      {result.quarantined_count}")
    print(f"  cost_usd:         ${result.llm_cost_usd:.5f}")

    if result.events_extracted:
        ev = result.events_extracted[0]
        print(f"\n  First event:")
        print(f"    event_type:      {ev.get('event_type')}")
        print(f"    confidence:      {ev.get('confidence_score', 0):.2f}")
        print(f"    summary:         {ev.get('summary', '')[:100]}")
        print("\nPASS — pipeline produced a stored event.")
    elif result.quarantined_count > 0:
        print("\nPASS (partial) — event extracted but confidence < 0.70, quarantined.")
    elif result.skipped_count > 0:
        print("\nPARTIAL — pre-filter classified content as not market-relevant.")
        print("Try adjusting the article or check PRE_FILTER_SYSTEM prompt.")
    else:
        print(f"\nFAIL — no events, no quarantine, no skip. error={result.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
