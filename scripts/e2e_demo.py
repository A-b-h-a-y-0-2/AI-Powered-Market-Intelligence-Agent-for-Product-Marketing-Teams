"""
End-to-end demonstration of the Market Intelligence Agent pipeline.

Shows each agent working with REAL storage (MongoDB + Supabase + Redis):
  0. Infrastructure health checks
  1. Research Agent  — live Tavily crawl for McKinsey
  2. Extraction Agent — Groq + Instructor → typed Pydantic events → MongoDB
  3. Storage overview — events in MongoDB by company
  4. Conversational Agent — query the knowledge base with grounded answer

Usage:
    uv run python scripts/e2e_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


def step(msg: str) -> None:
    print(f"  {YELLOW}▶{RESET}  {msg}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {DIM}   {msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")


async def main() -> None:
    print(f"\n{BOLD}Market Intelligence Agent — End-to-End Demo{RESET}")
    print(f"{DIM}Real storage: MongoDB + Supabase pgvector + Redis{RESET}")

    # ── Shared imports ────────────────────────────────────────────────────────
    from observability.logger import configure_logging
    from observability.tracing import init_tracing
    from storage.cache import CacheStore
    from storage.event_store import EventStore
    from storage.vector_store import VectorStore
    from tools.embedder import Embedder
    from tools.llm_adapter import LLMAdapter
    from tools.search import TavilySearch

    configure_logging("WARNING")  # quiet internal logs during demo
    init_tracing(public_key="", secret_key="", host="", enabled=False)

    # ── Wire storage ──────────────────────────────────────────────────────────
    cache = CacheStore(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"))
    event_store = EventStore(
        mongodb_uri=os.environ["MONGODB_URI"],
        db_name=os.environ.get("MONGODB_DB_NAME", "market_intelligence"),
    )
    vector_store = VectorStore(
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
    )

    # ── Wire tools ────────────────────────────────────────────────────────────
    embed_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    embedder = Embedder(api_key=embed_api_key)
    embed_label = "local fastembed 384-dim" if not embed_api_key else "OpenAI 1536-dim"

    llm = LLMAdapter(
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )

    tavily = TavilySearch(api_key=os.environ.get("TAVILY_API_KEY", ""))

    # ── Load model config (same as main.py) ───────────────────────────────────
    import yaml
    from pathlib import Path

    model_cfg = yaml.safe_load(Path(os.path.join(ROOT, "config/models.yaml")).read_text())
    model_routing = model_cfg["routing"]
    cost_config = model_cfg["costs"]

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 0 — Infrastructure health checks
    # ─────────────────────────────────────────────────────────────────────────
    section("STEP 0 — Infrastructure health checks")

    step("Connecting to MongoDB, Supabase, Redis …")
    await cache.connect()
    await event_store.connect()
    await vector_store.connect()
    await tavily.connect()

    redis_ok = await cache.health_check()
    mongo_ok = await event_store.health_check()
    supabase_ok = await vector_store.health_check()

    for name, healthy in [("MongoDB", mongo_ok), ("Supabase", supabase_ok), ("Redis", redis_ok)]:
        if healthy:
            ok(f"{name}: connected")
        else:
            fail(f"{name}: UNAVAILABLE")

    if not mongo_ok:
        fail("MongoDB is required — aborting")
        sys.exit(1)

    info(f"Embedder: {embed_label}")

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 1 — Research Agent: one live Tavily crawl
    # ─────────────────────────────────────────────────────────────────────────
    section("STEP 1 — Research Agent (live Tavily web search)")

    step("Searching: 'McKinsey AI partnerships technology announcement 2025 2026' …")
    t0 = time.monotonic()

    search_results = await tavily.search(
        query="McKinsey AI partnerships technology announcement 2025 2026",
        max_results=3,
        search_depth="advanced",
    )

    elapsed = time.monotonic() - t0

    crawl_inputs = []
    for r in search_results:
        url = r.url
        content = r.content[:5000]
        if url and content and len(content) > 200:
            crawl_inputs.append({"url": url, "content": content})

    ok(f"Got {len(crawl_inputs)} pages with content ({elapsed:.1f}s)")
    for ci in crawl_inputs:
        info(f"{ci['url'][:80]}")
        info(f"  → {len(ci['content'])} chars")

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 2 — Extraction Agent: raw content → structured events
    # ─────────────────────────────────────────────────────────────────────────
    section("STEP 2 — Extraction Agent (Groq + Instructor → MongoDB)")

    from agents.extraction_agent import ExtractionAgent
    from schemas.state import CrawlResult

    extraction_agent = ExtractionAgent(
        event_store=event_store,
        vector_store=vector_store,
        cache=cache,
        embedder=embedder,
        model_config=model_routing,
        cost_config=cost_config,
        llm_adapter=llm,
    )

    new_events: list[dict] = []
    run_id = f"e2e_demo_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    for i, ci in enumerate(crawl_inputs[:2]):
        step(f"Extracting page {i+1}/{min(2, len(crawl_inputs))}: {ci['url'][:55]}…")
        t0 = time.monotonic()

        import hashlib
        crawl_result = CrawlResult(
            url=ci["url"],
            content=ci["content"],
            content_hash=hashlib.sha256(ci["content"].encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
        )

        try:
            result = await extraction_agent.run(
                crawl_result=crawl_result,
                company="McKinsey & Company",
                run_id=run_id,
            )
            elapsed = time.monotonic() - t0

            if result.events_extracted:
                ok(
                    f"{len(result.events_extracted)} event(s) extracted "
                    f"({elapsed:.1f}s, cost ${result.llm_cost_usd:.5f})"
                )
                for ev in result.events_extracted:
                    info(f"  [{ev.get('event_type','?')}] conf={ev.get('confidence_score',0):.2f}")
                    info(f"    {ev.get('summary','')[:90]}")
                    info(f"    _id={ev.get('_id','?')}")
                new_events.extend(result.events_extracted)
            else:
                info(f"  No events extracted ({elapsed:.1f}s) — content was pre-filtered or low confidence")

        except Exception as exc:
            fail(f"Extraction failed: {exc}")

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 3 — Storage overview
    # ─────────────────────────────────────────────────────────────────────────
    section("STEP 3 — Storage (MongoDB knowledge base overview)")

    step("Reading all McKinsey events (last 90 days) …")
    mckinsey_events = await event_store.get_recent_events(
        company="McKinsey & Company", days=90, limit=10
    )
    ok(f"McKinsey events in KB: {len(mckinsey_events)}")
    for ev in mckinsey_events[:3]:
        info(f"  [{ev.get('event_type','?')}] {ev.get('summary','')[:80]}")
        info(f"    ts={ev.get('timestamp','?')}  conf={ev.get('confidence_score',0):.2f}")

    # total across all companies
    from collections import Counter
    all_companies = [
        "McKinsey & Company", "Boston Consulting Group", "Bain & Company",
        "Deloitte", "KPMG Advisory", "Deloitte Digital", "Oliver Wyman", "Accenture Strategy",
    ]
    print()
    totals: Counter = Counter()
    for co in all_companies:
        evs = await event_store.get_recent_events(company=co, days=90, limit=200)
        if evs:
            totals[co] = len(evs)

    ok(f"Total events across all companies: {sum(totals.values())}")
    for co, count in totals.most_common():
        info(f"  {co}: {count}")

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 4 — Conversational Agent: answer a real query
    # ─────────────────────────────────────────────────────────────────────────
    section("STEP 4 — Conversational Agent (grounded KB query)")

    from agents.conversational_agent import ConversationalAgent

    conv_agent = ConversationalAgent(
        event_store=event_store,
        vector_store=vector_store,
        cache=cache,
        embedder=embedder,
        tavily_search=tavily,
        model_config=model_routing,
        cost_config=cost_config,
        llm_adapter=llm,
    )

    query = "What has McKinsey been doing with AI and technology recently?"
    step(f'Query: "{query}"')
    step("Stakeholder role: sales")
    print()

    t0 = time.monotonic()
    session_id = f"demo_{uuid.uuid4().hex[:8]}"

    answer_chunks: list[dict] = []
    async for chunk in conv_agent.stream(
        message=query,
        session_id=session_id,
        stakeholder_role="sales",
    ):
        answer_chunks.append(chunk)
        if chunk.get("type") == "status":
            info(f"[status] {chunk.get('content', '')}")

    elapsed = time.monotonic() - t0

    # find the final "done" chunk
    done = next((c for c in reversed(answer_chunks) if c.get("type") == "done"), None)
    if done:
        print(f"\n{BOLD}  Answer:{RESET}")
        for line in done.get("content", "").split("\n"):
            print(f"    {line}")

        sources = done.get("sources", [])
        if sources:
            print()
            ok(f"Sources ({len(sources)}):")
            for src in sources[:4]:
                if isinstance(src, dict):
                    info(f"  {src.get('url', src)[:90]}")
                else:
                    info(f"  {str(src)[:90]}")

        confidence = done.get("confidence", done.get("confidence_score"))
        if confidence is not None:
            ok(f"Confidence: {confidence:.2f}")
    else:
        fail("No 'done' chunk received from conversational agent")

    ok(f"Response time: {elapsed:.1f}s")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    section("DONE")
    ok("Pipeline complete: research → extraction → storage → conversational query")

    await event_store.disconnect()
    await cache.disconnect()
    print()


if __name__ == "__main__":
    asyncio.run(main())
