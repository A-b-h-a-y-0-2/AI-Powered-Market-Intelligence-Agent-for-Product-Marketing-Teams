"""
Full pipeline demonstration across 3 competitors.

Per competitor:
  1. Research Agent  — live Tavily search (targeted query for specific event type)
  2. Extraction Agent — Groq + Instructor → typed Pydantic event → MongoDB
  3. Embedder         — local fastembed → Supabase pgvector

Then for all three:
  4. Conversational Agent — one query per competitor, from the KB

Targets:
  McKinsey & Company   → PARTNERSHIP   (AI alliance announcements)
  Boston Consulting Group → ACQUISITION / FEATURE_LAUNCH (BCG X / AI product)
  Bain & Company       → HIRING_TREND  (consulting hiring signals)

Usage:
    uv run python scripts/full_pipeline_demo.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# ── colour helpers ────────────────────────────────────────────────────────────
BOLD  = "\033[1m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
CYAN  = "\033[36m"; RED   = "\033[31m"; DIM    = "\033[2m"; RESET = "\033[0m"
MAGENTA = "\033[35m"

def banner(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'═'*64}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'═'*64}{RESET}")

def section(title: str) -> None:
    print(f"\n{BOLD}{MAGENTA}  ── {title} ──{RESET}")

def step(msg: str)  -> None: print(f"  {YELLOW}▶{RESET}  {msg}")
def ok(msg: str)    -> None: print(f"  {GREEN}✓{RESET}  {msg}")
def info(msg: str)  -> None: print(f"  {DIM}   {msg}{RESET}")
def fail(msg: str)  -> None: print(f"  {RED}✗{RESET}  {msg}")

# ── competitor targets ────────────────────────────────────────────────────────
TARGETS = [
    {
        "company": "McKinsey & Company",
        "label": "McKinsey",
        "goal_event_type": "PARTNERSHIP",
        "search_query": "\"McKinsey\" partnership alliance announcement site:mckinsey.com OR site:businesswire.com OR site:prnewswire.com 2025",
        "conv_query": "What partnerships has McKinsey formed around AI recently?",
        "include_domains": ["mckinsey.com", "businesswire.com", "prnewswire.com", "reuters.com", "ft.com"],
    },
    {
        "company": "Boston Consulting Group",
        "label": "BCG",
        "goal_event_type": "ACQUISITION / FEATURE_LAUNCH",
        "search_query": "\"Boston Consulting Group\" OR \"BCG\" new service offering product launch acquisition announcement 2025",
        "conv_query": "What new products or acquisitions has BCG announced recently?",
        "include_domains": ["bcg.com", "businesswire.com", "prnewswire.com", "reuters.com"],
    },
    {
        "company": "Bain & Company",
        "label": "Bain",
        "goal_event_type": "HIRING_TREND / PRODUCT_UPDATE",
        "search_query": "\"Bain & Company\" OR \"Bain and Company\" consulting hiring practice launch product update 2025",
        "conv_query": "What can you tell me about Bain's recent hiring and product moves?",
        "include_domains": ["bain.com", "businesswire.com", "prnewswire.com", "reuters.com"],
    },
]


async def wire_infra():
    """Connect all storage and tools; return them as a dict."""
    from observability.logger import configure_logging
    from observability.tracing import init_tracing
    from storage.cache import CacheStore
    from storage.event_store import EventStore
    from storage.vector_store import VectorStore
    from tools.embedder import Embedder
    from tools.llm_adapter import LLMAdapter
    from tools.search import TavilySearch
    import yaml
    from pathlib import Path

    configure_logging("WARNING")
    init_tracing(public_key="", secret_key="", host="", enabled=False)

    cache = CacheStore(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"))
    event_store = EventStore(
        mongodb_uri=os.environ["MONGODB_URI"],
        db_name=os.environ.get("MONGODB_DB_NAME", "market_intelligence"),
    )
    vector_store = VectorStore(
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
    )
    embedder = Embedder(api_key=os.environ.get("OPENAI_API_KEY", "").strip())
    llm = LLMAdapter(
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )
    tavily = TavilySearch(api_key=os.environ.get("TAVILY_API_KEY", ""))

    model_cfg = yaml.safe_load(
        Path(os.path.join(ROOT, "config/models.yaml")).read_text()
    )

    await cache.connect()
    await event_store.connect()
    await vector_store.connect()
    await tavily.connect()

    return dict(
        cache=cache, event_store=event_store, vector_store=vector_store,
        embedder=embedder, llm=llm, tavily=tavily,
        model_routing=model_cfg["routing"], cost_config=model_cfg["costs"],
    )


async def research_one(target: dict, tavily, max_pages: int = 3) -> list[dict]:
    """Step 1: live Tavily search → crawl inputs."""
    results = await tavily.search(
        query=target["search_query"],
        max_results=max_pages,
        search_depth="advanced",
        include_domains=target.get("include_domains"),
    )
    pages = []
    for r in results:
        content = r.content[:5000]
        if r.url and content and len(content) > 200:
            pages.append({"url": r.url, "content": content})
    return pages


async def extract_one(
    target: dict, pages: list[dict], infra: dict, run_id: str
) -> list[dict]:
    """Step 2: Extraction Agent — raw pages → structured events in MongoDB+Supabase."""
    from agents.extraction_agent import ExtractionAgent
    from schemas.state import CrawlResult

    agent = ExtractionAgent(
        event_store=infra["event_store"],
        vector_store=infra["vector_store"],
        cache=infra["cache"],
        embedder=infra["embedder"],
        model_config=infra["model_routing"],
        cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    extracted: list[dict] = []
    for page in pages[:2]:
        crawl_result = CrawlResult(
            url=page["url"],
            content=page["content"],
            content_hash=hashlib.sha256(page["content"].encode()).hexdigest(),
            status_code=200,
            is_changed=True,
            crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
        )
        result = await agent.run(
            crawl_result=crawl_result,
            company=target["company"],
            run_id=run_id,
        )
        extracted.extend(result.events_extracted)
        total_cost = sum(
            ev.get("cost_usd", 0) or 0 for ev in result.events_extracted
        ) or result.llm_cost_usd
        if result.events_extracted:
            ok(
                f"  [{target['label']}] {len(result.events_extracted)} event(s) "
                f"extracted  cost ${result.llm_cost_usd:.5f}"
            )
            for ev in result.events_extracted:
                etype = str(ev.get("event_type", "?")).upper().replace("EVENTTYPE.", "")
                conf  = ev.get("confidence_score", 0)
                summ  = ev.get("summary", "")[:90]
                info(f"    type={etype}  conf={conf:.2f}")
                info(f"    {summ}")
        else:
            info(f"  [{target['label']}] no events from {page['url'][:55]}…")

    return extracted


async def query_one(target: dict, infra: dict) -> None:
    """Step 4: Conversational Agent — query the KB for this competitor."""
    from agents.conversational_agent import ConversationalAgent

    agent = ConversationalAgent(
        event_store=infra["event_store"],
        vector_store=infra["vector_store"],
        cache=infra["cache"],
        embedder=infra["embedder"],
        tavily_search=infra["tavily"],
        model_config=infra["model_routing"],
        cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    query = target["conv_query"]
    session_id = f"demo_{uuid.uuid4().hex[:8]}"
    step(f'[{target["label"]}] "{query}"')

    t0 = time.monotonic()
    status_msgs: list[str] = []
    done_chunk = None

    async for chunk in agent.stream(
        message=query, session_id=session_id, stakeholder_role="sales"
    ):
        if chunk.get("type") == "status":
            status_msgs.append(chunk.get("content", ""))
        elif chunk.get("type") == "done":
            done_chunk = chunk
        elif chunk.get("type") == "error":
            fail(f"  Agent error: {chunk.get('content','')}")
            return

    elapsed = time.monotonic() - t0
    info(f"  Pipeline: {' → '.join(status_msgs)}")

    if done_chunk:
        answer = done_chunk.get("content", "")
        sources = done_chunk.get("sources", [])
        confidence = done_chunk.get("confidence", done_chunk.get("confidence_score"))
        is_live = done_chunk.get("is_live_fallback", False)

        print()
        print(f"  {BOLD}Answer ({target['label']}):{RESET}")
        for line in answer.split("\n"):
            if line.strip():
                print(f"    {line}")

        print()
        retrieval = "Tavily live" if is_live else "KB (pgvector)"
        ok(f"  Retrieved from: {retrieval}  |  conf={confidence:.2f}  |  {elapsed:.1f}s")
        if sources:
            ok(f"  Sources ({len(sources)}):")
            for src in sources[:3]:
                url = src.get("url", src) if isinstance(src, dict) else str(src)
                info(f"    {url[:85]}")
    else:
        fail(f"  No answer returned ({elapsed:.1f}s)")


# ─────────────────────────────────────────────────────────────────────────────
async def main() -> None:
    banner("Market Intelligence Agent — Full Pipeline Demo")
    print(f"  {DIM}3 competitors · different event types · MongoDB + Supabase + Redis{RESET}\n")

    # ── STEP 0: infrastructure ────────────────────────────────────────────────
    section("STEP 0 — Infrastructure")
    step("Connecting MongoDB, Supabase, Redis …")
    infra = await wire_infra()

    mongo_ok   = await infra["event_store"].health_check()
    supa_ok    = await infra["vector_store"].health_check()
    redis_ok   = await infra["cache"].health_check()
    embed_mode = "local fastembed 384-dim" if not os.environ.get("OPENAI_API_KEY","").strip() else "OpenAI 1536-dim"

    for name, healthy in [("MongoDB", mongo_ok), ("Supabase", supa_ok), ("Redis", redis_ok)]:
        (ok if healthy else fail)(f"{name}: {'connected' if healthy else 'UNAVAILABLE'}")
    info(f"Embedder: {embed_mode}")

    if not mongo_ok:
        fail("MongoDB required — aborting"); sys.exit(1)

    # ── STEPS 1–3: research + extraction per competitor ───────────────────────
    run_id = f"full_demo_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    all_extracted: dict[str, list[dict]] = defaultdict(list)

    for target in TARGETS:
        section(
            f"STEP 1+2+3 — {target['label']}  [target: {target['goal_event_type']}]"
        )

        # 1. Research
        step(f"[{target['label']}] Tavily search …")
        t0 = time.monotonic()
        pages = await research_one(target, infra["tavily"], max_pages=3)
        ok(f"  [{target['label']}] {len(pages)} page(s) fetched ({time.monotonic()-t0:.1f}s)")
        for p in pages:
            info(f"    {p['url'][:75]}  ({len(p['content'])} chars)")

        if not pages:
            fail(f"  No crawl results for {target['label']} — skipping extraction")
            continue

        # 2+3. Extraction + embed + store
        step(f"[{target['label']}] Extracting + storing …")
        events = await extract_one(target, pages, infra, run_id)
        all_extracted[target["company"]] = events

        if not events:
            info(f"  [{target['label']}] Content pre-filtered or low confidence — no new events")

    # ── STEP 3.5: KB snapshot ─────────────────────────────────────────────────
    section("STEP 3.5 — Knowledge Base Snapshot")
    step("MongoDB event counts per competitor …")
    from collections import Counter
    totals: Counter = Counter()
    by_type: dict[str, Counter] = {}
    for target in TARGETS:
        co = target["company"]
        evs = await infra["event_store"].get_recent_events(company=co, days=180, limit=200)
        totals[co] = len(evs)
        by_type[co] = Counter(str(e.get("event_type","?")).replace("EventType.","") for e in evs)

    ok(f"Total across 3 competitors: {sum(totals.values())} events")
    for target in TARGETS:
        co = target["company"]
        type_str = ", ".join(f"{t}×{c}" for t, c in by_type[co].most_common(4))
        info(f"  {target['label']:<8} {totals[co]:>3} events  [{type_str}]")

    newly = sum(len(v) for v in all_extracted.values())
    ok(f"New events ingested this run: {newly}")
    for co, evs in all_extracted.items():
        label = next(t["label"] for t in TARGETS if t["company"] == co)
        for ev in evs:
            etype = str(ev.get("event_type","?")).replace("EventType.","").upper()
            info(f"  {label}: [{etype}] {ev.get('summary','')[:75]}")

    # ── STEP 4: Conversational queries ────────────────────────────────────────
    section("STEP 4 — Conversational Agent (one query per competitor)")

    for target in TARGETS:
        print()
        await query_one(target, infra)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    await infra["event_store"].disconnect()
    await infra["cache"].disconnect()

    banner("DONE")
    ok("research → extraction → ingestion → vector embed → conversational query")
    print()


if __name__ == "__main__":
    asyncio.run(main())
