"""Smoke test — exercises real LLM calls with mocked storage.

Requires only:
    GROQ_API_KEY
    OPENROUTER_API_KEY

No MongoDB, Redis, or Supabase needed.

Run:
    source .venv/bin/activate
    cp .env.example .env   # fill in GROQ_API_KEY + OPENROUTER_API_KEY
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from observability.logger import configure_logging, get_logger
from observability.tracing import init_tracing

configure_logging("INFO")

# Use real Langfuse if keys are present, otherwise no-op
_lf_public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
_lf_secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
_lf_host   = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
init_tracing(
    public_key=_lf_public or "smoke",
    secret_key=_lf_secret or "smoke",
    host=_lf_host,
    enabled=bool(_lf_public and _lf_secret),
)

log = get_logger("smoke_test")

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

if not GROQ_KEY or not OPENROUTER_KEY:
    print("ERROR: Set GROQ_API_KEY and OPENROUTER_API_KEY in .env before running.")
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _ok(label: str, value: str = "") -> None:
    print(f"  [OK]  {label}" + (f": {value}" if value else ""))


def _fail(label: str, err: str) -> None:
    print(f"  [FAIL] {label}: {err}")


# ── Test 1: LLMAdapter + Embedder ────────────────────────────────────────────

async def test_llm_adapter() -> None:
    _section("Test 1: LLMAdapter — Groq (Llama) + OpenRouter (Claude) + OpenRouter (Embed)")

    from tools.llm_adapter import LLMAdapter
    adapter = LLMAdapter(groq_api_key=GROQ_KEY, openrouter_api_key=OPENROUTER_KEY)

    # 1a. Groq call (Llama)
    try:
        client = adapter.get_chat_client("llama-3.3-70b-versatile")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with exactly: GROQ_OK"}],
        )
        text = response.choices[0].message.content.strip()
        _ok("Groq (llama-3.3-70b-versatile)", text)
    except Exception as exc:
        _fail("Groq call", str(exc))

    # 1b. OpenRouter call (Claude Haiku)
    try:
        client = adapter.get_chat_client("anthropic/claude-haiku-4.5")
        response = client.chat.completions.create(
            model="anthropic/claude-haiku-4.5",
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with exactly: OPENROUTER_OK"}],
        )
        text = response.choices[0].message.content.strip()
        _ok("OpenRouter (claude-haiku-4.5)", text)
    except Exception as exc:
        _fail("OpenRouter call", str(exc))

    # 1c. OpenRouter embeddings (openai/text-embedding-3-small via OpenRouter)
    try:
        from tools.embedder import Embedder
        embedder = Embedder(
            api_key=OPENROUTER_KEY,
            base_url="https://openrouter.ai/api/v1",
            model="openai/text-embedding-3-small",
        )
        vec = await embedder.embed("competitor product launch announcement")
        _ok("OpenRouter embeddings (text-embedding-3-small)", f"dims={len(vec)}")
    except Exception as exc:
        _fail("OpenRouter embeddings", str(exc))


# ── Test 2: ExtractionAgent — pre-filter + extraction ─────────────────────────

async def test_extraction_agent() -> None:
    _section("Test 2: ExtractionAgent — pre-filter + structured extraction")

    from tools.llm_adapter import LLMAdapter
    from agents.extraction_agent import ExtractionAgent
    from schemas.state import CrawlResult

    adapter = LLMAdapter(groq_api_key=GROQ_KEY, openrouter_api_key=OPENROUTER_KEY)

    # Mock all storage — no DB needed
    event_store = MagicMock()
    event_store.insert_event = AsyncMock(return_value="event_test_001")
    event_store.insert_quarantined_event = AsyncMock()
    event_store.add_source_to_event = AsyncMock()
    event_store.get_recent_events = AsyncMock(return_value=[])

    vector_store = MagicMock()
    vector_store.upsert_embedding = AsyncMock()

    cache = MagicMock()
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 1536)
    embedder.embed_batch = AsyncMock(return_value=[[0.1] * 1536])

    model_cfg = {
        "pre_filter": "anthropic/claude-haiku-4.5",
        "extraction": "llama-3.3-70b-versatile",
        "validation": "anthropic/claude-sonnet-4-6",
    }
    cost_cfg = {
        "anthropic/claude-haiku-4.5": {"input": 0.00025, "output": 0.00125},
        "llama-3.3-70b-versatile": {"input": 0.00059, "output": 0.00079},
    }

    agent = ExtractionAgent(
        event_store=event_store,
        vector_store=vector_store,
        cache=cache,
        embedder=embedder,
        model_config=model_cfg,
        cost_config=cost_cfg,
        llm_adapter=adapter,
    )

    # A realistic changelog article
    content = """
    # Acme Corp Launches Enterprise SSO and SOC 2 Type II Certification

    Acme Corp today announced the general availability of Enterprise SSO
    (SAML 2.0) across all Pro and Enterprise plans, effective immediately.
    The company also received its SOC 2 Type II certification this week,
    which the team says was driven by demand from Fortune 500 customers.

    Pricing for Enterprise SSO is included in existing Enterprise plans.
    Pro plan customers can add it for $49/seat/month.
    """

    crawl = CrawlResult(
        url="https://acme.example.com/blog/enterprise-sso",
        content=content,
        is_changed=True,
        crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
        content_hash="abc123",
        status_code=200,
    )

    try:
        result = await agent.run(crawl_result=crawl, company="Acme Corp", run_id="smoke_001")
        if result.error_message:
            _fail("ExtractionAgent.run", result.error_message)
        elif result.skipped_count > 0:
            _ok("ExtractionAgent pre-filter", "content was filtered as not market-relevant (adjust content or model)")
        elif result.quarantined_count > 0:
            _ok("ExtractionAgent quarantine", "event extracted but confidence < 0.70 → quarantined")
        elif result.events_extracted:
            ev = result.events_extracted[0]
            _ok("ExtractionAgent stored event", f"type={ev.get('event_type')} confidence={ev.get('confidence_score'):.2f}")
        else:
            _ok("ExtractionAgent", f"result: skipped={result.skipped_count} quarantined={result.quarantined_count} stored={len(result.events_extracted)}")
        _ok("Total LLM cost", f"${result.llm_cost_usd:.5f}")
    except Exception as exc:
        _fail("ExtractionAgent.run", str(exc))


# ── Test 3: ConversationalAgent — single query (no KB data, KB miss → Tavily) ──

async def test_conversational_agent() -> None:
    _section("Test 3: ConversationalAgent — scope detection + query classification")

    from tools.llm_adapter import LLMAdapter
    from agents.conversational_agent import ConversationalAgent

    adapter = LLMAdapter(groq_api_key=GROQ_KEY, openrouter_api_key=OPENROUTER_KEY)

    # Mock all storage and search
    event_store = MagicMock()
    event_store.get_recent_events = AsyncMock(return_value=[])
    event_store.get_events_by_stakeholder = AsyncMock(return_value=[])
    event_store.get_threat_score = AsyncMock(return_value=None)
    event_store.get_event_by_id = AsyncMock(return_value=None)
    event_store.upsert_enriched_fact = AsyncMock()

    vector_store = MagicMock()
    vector_store.semantic_search = AsyncMock(return_value=[])

    cache = MagicMock()
    cache.get_session_history = AsyncMock(return_value=[])
    cache.save_session_history = AsyncMock()

    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 1536)

    tavily = MagicMock()
    tavily.search_company_news = AsyncMock(return_value=[])

    model_cfg = {
        "pre_filter": "anthropic/claude-haiku-4.5",
        "conversational": "anthropic/claude-haiku-4.5",  # use haiku to save cost
        "synthesis": "anthropic/claude-haiku-4.5",
    }
    cost_cfg = {
        "anthropic/claude-haiku-4.5": {"input": 0.00025, "output": 0.00125},
    }

    agent = ConversationalAgent(
        event_store=event_store,
        vector_store=vector_store,
        cache=cache,
        embedder=embedder,
        tavily_search=tavily,
        model_config=model_cfg,
        cost_config=cost_cfg,
        llm_adapter=adapter,
    )

    # Test 3a: out-of-scope query
    print("\n  Query: 'Who won the World Cup?'")
    events = []
    async for chunk in agent.stream("Who won the World Cup?", session_id="smoke-session"):
        events.append(chunk)
    final = next((c for c in events if c.get("type") == "done"), None)
    if final:
        _ok("Out-of-scope detection", final["content"][:80])
    else:
        _fail("out-of-scope", f"events={events}")

    # Test 3b: in-scope query (KB will be empty → coverage miss path)
    print("\n  Query: 'What did Competitor A ship this week?'")
    events = []
    async for chunk in agent.stream(
        "What did Competitor A ship this week?",
        session_id="smoke-session-2",
        stakeholder_role="marketing",
    ):
        events.append(chunk)
    statuses = [c.get("content", "") for c in events if c.get("type") == "status"]
    final = next((c for c in events if c.get("type") == "done"), None)
    print(f"    Pipeline steps: {statuses}")
    if final:
        _ok("Conversational response", final["content"][:120])
        _ok("Confidence", str(final.get("confidence")))
        _ok("Sources", str(final.get("sources", [])))
    else:
        _fail("conversational", f"got chunks: {[c.get('type') for c in events]}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\nMarket Intelligence Agent — Smoke Test")
    print(f"GROQ_API_KEY:       {GROQ_KEY[:8]}...")
    print(f"OPENROUTER_API_KEY: {OPENROUTER_KEY[:8]}...")

    await test_llm_adapter()
    await test_extraction_agent()
    await test_conversational_agent()

    print(f"\n{'='*60}")
    print("  Smoke test complete. Check [OK]/[FAIL] lines above.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
