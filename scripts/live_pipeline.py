"""
Full Market Intelligence Pipeline — Live Dashboard

Runs every agent in sequence with a real-time Rich dashboard showing:
  • Agent status table (PENDING → RUNNING → DONE/FAILED)
  • Live scrolling event log (what's happening right now)
  • Running cost tracker
  • KB metrics (events, embeddings, narratives, threat scores)

Agent sequence:
  0. Infrastructure health
  1. Research Agent       → Tavily crawl (McKinsey, BCG, Bain)
  2. Extraction Agent     → Groq + Instructor → MongoDB + Supabase
  3. Matrix Agent         → FeatureLaunch/ProductUpdate → feature matrix
  4. Sentiment Agent      → ABSA on review text → CustomerSentimentEvent
  5. Hiring Signal Agent  → hiring_trend events → WeakSignalPrediction
  6. Narrative Agent      → event clusters → NarrativeEvent per company
  7. Convergence Agent    → cross-competitor clusters → MarketTrendEvent
  8. Threat Scoring Agent → velocity+type+recency → ThreatScore per company
  9. Digest Agent         → weekly brief per stakeholder role
 10. Intelligence Agent   → on-demand stakeholder insight (McKinsey, role=sales)
 11. Conversational Agent → 3 queries, one per competitor

Usage:
    uv run python scripts/live_pipeline.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# ── Rich imports ──────────────────────────────────────────────────────────────
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

# ── State shared between the runner and the dashboard ────────────────────────
COMPANIES = ["McKinsey & Company", "Boston Consulting Group", "Bain & Company"]
LABELS    = {"McKinsey & Company": "McKinsey", "Boston Consulting Group": "BCG", "Bain & Company": "Bain"}

AGENTS = [
    ("infra",        "Infrastructure"),
    ("research",     "Research Agent"),
    ("extraction",   "Extraction Agent"),
    ("matrix",       "Matrix Agent"),
    ("sentiment",    "Sentiment Agent"),
    ("hiring",       "Hiring Signal Agent"),
    ("narrative",    "Narrative Agent"),
    ("convergence",  "Convergence Agent"),
    ("threat",       "Threat Scoring Agent"),
    ("digest",       "Digest Agent"),
    ("intelligence", "Intelligence Agent"),
    ("conv",         "Conversational Agent"),
]

STATUS_STYLE = {
    "PENDING": ("dim", "·"),
    "RUNNING": ("bold yellow", "⟳"),
    "DONE":    ("bold green",  "✓"),
    "FAILED":  ("bold red",    "✗"),
    "SKIPPED": ("dim cyan",    "–"),
}

state: dict[str, Any] = {
    "agent_status": {k: "PENDING" for k, _ in AGENTS},
    "log": deque(maxlen=28),
    "metrics": {
        "events_total": 0, "events_new": 0,
        "narratives": 0, "threat_scores": 0,
        "predictions": 0, "sentiment_events": 0,
        "matrix_updates": 0, "digests": 0,
        "cost_usd": 0.0,
    },
    "results": {},
    "start_ts": time.monotonic(),
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    state["log"].append(f"[dim]{ts}[/dim]  {msg}")


def set_status(key: str, status: str) -> None:
    state["agent_status"][key] = status
    log(f"[{'bold yellow' if status=='RUNNING' else 'bold green' if status=='DONE' else 'bold red'}]{status}[/]  {dict(AGENTS)[key]}")


# ── Dashboard renderer ────────────────────────────────────────────────────────
def make_status_table() -> Table:
    t = Table(box=box.SIMPLE, show_header=False, pad_edge=False, expand=True)
    t.add_column("ic",   width=2, no_wrap=True)
    t.add_column("name", no_wrap=True)
    t.add_column("st",   width=8, no_wrap=True)

    for key, label in AGENTS:
        st = state["agent_status"][key]
        style, icon = STATUS_STYLE[st]
        t.add_row(
            Text(icon, style=style),
            Text(label, style=style),
            Text(st,   style=style),
        )
    return t


def make_metrics_table() -> Table:
    m = state["metrics"]
    elapsed = time.monotonic() - state["start_ts"]
    t = Table(box=box.SIMPLE, show_header=False, pad_edge=False, expand=True)
    t.add_column("k", style="dim", no_wrap=True)
    t.add_column("v", style="bold cyan", no_wrap=True)
    rows = [
        ("Events in KB",    str(m["events_total"])),
        ("New this run",    str(m["events_new"])),
        ("Narratives",      str(m["narratives"])),
        ("Threat scores",   str(m["threat_scores"])),
        ("Predictions",     str(m["predictions"])),
        ("Sentiment evts",  str(m["sentiment_events"])),
        ("Matrix updates",  str(m["matrix_updates"])),
        ("Digests",         str(m["digests"])),
        ("LLM cost",       f"${m['cost_usd']:.4f}"),
        ("Elapsed",        f"{elapsed:.0f}s"),
    ]
    for k, v in rows:
        t.add_row(k, v)
    return t


def make_log_panel() -> Panel:
    lines = list(state["log"])
    content = "\n".join(lines[-24:]) if lines else "[dim]Waiting…[/dim]"
    return Panel(content, title="[bold]Live Event Log[/bold]", border_style="cyan", expand=True)


def make_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(name="left",  ratio=1),
        Layout(name="right", ratio=2),
    )
    layout["left"].split_column(
        Layout(name="status",  ratio=3),
        Layout(name="metrics", ratio=2),
    )
    return layout


def update_layout(layout: Layout) -> None:
    elapsed = time.monotonic() - state["start_ts"]
    layout["header"].update(Panel(
        f"[bold cyan]Market Intelligence Agent — Full Pipeline Run[/bold cyan]"
        f"  [dim]|  {datetime.now().strftime('%H:%M:%S')}  |  elapsed {elapsed:.0f}s[/dim]",
        box=box.HORIZONTALS,
    ))
    layout["status"].update(Panel(make_status_table(), title="[bold]Agent Status[/bold]", border_style="magenta"))
    layout["metrics"].update(Panel(make_metrics_table(), title="[bold]KB Metrics[/bold]", border_style="blue"))
    layout["right"].update(make_log_panel())


# ═════════════════════════════════════════════════════════════════════════════
#  Wire infrastructure
# ═════════════════════════════════════════════════════════════════════════════
async def wire() -> dict:
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

    configure_logging("ERROR")
    langfuse_pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    langfuse_sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_host = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
    langfuse_enabled = bool(langfuse_pub and langfuse_sec)
    init_tracing(
        public_key=langfuse_pub,
        secret_key=langfuse_sec,
        host=langfuse_host,
        enabled=langfuse_enabled,
    )

    cache       = CacheStore(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"))
    event_store = EventStore(mongodb_uri=os.environ["MONGODB_URI"],
                             db_name=os.environ.get("MONGODB_DB_NAME", "market_intelligence"))
    vector_store = VectorStore(supabase_url=os.environ.get("SUPABASE_URL", ""),
                               supabase_key=os.environ.get("SUPABASE_SERVICE_KEY", ""))
    embedder = Embedder(api_key=os.environ.get("OPENAI_API_KEY", "").strip())
    llm      = LLMAdapter(groq_api_key=os.environ.get("GROQ_API_KEY", ""),
                          openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""))
    tavily   = TavilySearch(api_key=os.environ.get("TAVILY_API_KEY", ""))

    model_cfg    = yaml.safe_load(Path(os.path.join(ROOT, "config/models.yaml")).read_text())
    model_routing = model_cfg["routing"]
    cost_config   = model_cfg["costs"]

    await cache.connect()
    await event_store.connect()
    await vector_store.connect()
    await tavily.connect()

    return dict(cache=cache, event_store=event_store, vector_store=vector_store,
                embedder=embedder, llm=llm, tavily=tavily,
                model_routing=model_routing, cost_config=cost_config)


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 0 — Infrastructure
# ═════════════════════════════════════════════════════════════════════════════
async def stage_infra(infra: dict) -> None:
    set_status("infra", "RUNNING")
    await asyncio.sleep(0.1)

    mongo_ok  = await infra["event_store"].health_check()
    supa_ok   = await infra["vector_store"].health_check()
    redis_ok  = await infra["cache"].health_check()

    embed_mode = ("local fastembed 384-dim"
                  if not os.environ.get("OPENAI_API_KEY", "").strip()
                  else "OpenAI 1536-dim")

    log(f"MongoDB: {'[green]connected[/]' if mongo_ok else '[red]UNAVAILABLE[/]'}")
    log(f"Supabase: {'[green]connected[/]' if supa_ok else '[red]UNAVAILABLE[/]'}")
    log(f"Redis: {'[green]connected[/]' if redis_ok else '[red]UNAVAILABLE[/]'}")
    log(f"Embedder: [cyan]{embed_mode}[/]")
    log(f"LLM: [cyan]extraction=llama-3.3-70b  synthesis/conv=llama-3.1-8b[/]")

    if not mongo_ok:
        set_status("infra", "FAILED")
        raise RuntimeError("MongoDB unavailable")

    # initial count
    total = sum([
        len(await infra["event_store"].get_recent_events(company=c, days=180, limit=200))
        for c in COMPANIES
    ])
    state["metrics"]["events_total"] = total
    log(f"KB has [bold]{total}[/] existing events across {len(COMPANIES)} companies")
    set_status("infra", "DONE")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 1+2 — Research + Extraction (one article per company)
# ═════════════════════════════════════════════════════════════════════════════
SEARCH_TARGETS = [
    ("McKinsey & Company", "mckinsey.com partnership alliance AI announcement",
     ["mckinsey.com", "businesswire.com"]),
    ("Boston Consulting Group", "BCG Boston Consulting Group acquisition product launch AI",
     ["bcg.com", "businesswire.com", "prnewswire.com"]),
    ("Bain & Company", "Bain Company consulting hiring product update announcement",
     ["bain.com", "businesswire.com", "prnewswire.com"]),
]


async def stage_research_extraction(infra: dict) -> None:
    from agents.extraction_agent import ExtractionAgent
    from schemas.state import CrawlResult

    set_status("research", "RUNNING")
    extraction_agent = ExtractionAgent(
        event_store=infra["event_store"], vector_store=infra["vector_store"],
        cache=infra["cache"], embedder=infra["embedder"],
        model_config=infra["model_routing"], cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )
    run_id = f"live_{datetime.now(tz=timezone.utc).strftime('%H%M%S')}"

    total_new = 0
    total_cost = 0.0

    for company, query, domains in SEARCH_TARGETS:
        label = LABELS[company]
        log(f"[yellow]Research[/] [{label}] searching: [dim]{query[:55]}…[/]")
        try:
            pages = await infra["tavily"].search(
                query=query, max_results=2, search_depth="advanced",
                include_domains=domains,
            )
            pages = [p for p in pages if p.content and len(p.content) > 200]
            log(f"[yellow]Research[/] [{label}] → {len(pages)} page(s)")
        except Exception as e:
            log(f"[red]Research[/] [{label}] search failed: {str(e)[:60]}")
            pages = []

        set_status("extraction", "RUNNING")
        for page in pages[:1]:  # one page per company to preserve tokens
            log(f"[yellow]Extract[/] [{label}] {page.url[:55]}…")
            try:
                crawl = CrawlResult(
                    url=page.url,
                    content=page.content[:5000],
                    content_hash=hashlib.sha256(page.content.encode()).hexdigest(),
                    status_code=200, is_changed=True,
                    crawl_timestamp=datetime.now(tz=timezone.utc).isoformat(),
                )
                result = await extraction_agent.run(
                    crawl_result=crawl, company=company, run_id=run_id
                )
                n = len(result.events_extracted)
                total_new  += n
                total_cost += result.llm_cost_usd
                state["metrics"]["cost_usd"] += result.llm_cost_usd

                if n:
                    for ev in result.events_extracted:
                        etype = str(ev.get("event_type","?")).replace("EventType.","").upper()
                        conf  = ev.get("confidence_score", 0)
                        log(f"[green]Stored[/] [{label}] [bold]{etype}[/] conf={conf:.2f} → MongoDB+Supabase")
                else:
                    log(f"[dim]Extract[/] [{label}] pre-filtered (not market-relevant)")

            except Exception as e:
                log(f"[red]Extract[/] [{label}] error: {str(e)[:70]}")

    state["metrics"]["events_new"]  += total_new
    state["metrics"]["events_total"] += total_new

    set_status("research",   "DONE")
    set_status("extraction", "DONE")
    log(f"Research+Extraction complete: [bold]{total_new}[/] new events, cost=[cyan]${total_cost:.5f}[/]")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 3 — Matrix Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_matrix(infra: dict) -> None:
    from agents.matrix_agent import MatrixAgent
    set_status("matrix", "RUNNING")
    log("[yellow]Matrix[/] Scanning for FeatureLaunch/ProductUpdate events…")

    agent = MatrixAgent(
        event_store=infra["event_store"], cache=infra["cache"],
        model_config=infra["model_routing"], cost_config=infra["cost_config"],
    )

    updates = 0
    for company in COMPANIES:
        label = LABELS[company]
        events = await infra["event_store"].get_recent_events(
            company=company, days=90,
            event_types=["feature_launch", "product_update", "FeatureLaunch", "ProductUpdate"],
            limit=5,
        )
        for ev in events[:2]:
            try:
                r = await agent.run(event=ev)
                if r.get("action") == "updated":
                    updates += 1
                    log(f"[green]Matrix[/] [{label}] updated [{r.get('category','?')}]: {r.get('feature_name','?')[:50]}")
                else:
                    log(f"[dim]Matrix[/] [{label}] skipped ({r.get('action','?')})")
                state["metrics"]["cost_usd"] += r.get("cost_usd", 0) or 0
            except Exception as e:
                log(f"[red]Matrix[/] [{label}] error: {str(e)[:60]}")

    state["metrics"]["matrix_updates"] = updates
    set_status("matrix", "DONE")
    log(f"Matrix Agent: [bold]{updates}[/] feature matrix cell(s) updated")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 4 — Sentiment Agent (ABSA)
# ═════════════════════════════════════════════════════════════════════════════
async def stage_sentiment(infra: dict) -> None:
    from agents.sentiment_agent import SentimentAgent, ReviewBatch
    set_status("sentiment", "RUNNING")
    log("[yellow]Sentiment[/] Running ABSA on existing event summaries as review proxies…")

    agent = SentimentAgent(
        event_store=infra["event_store"],
        model_config=infra["model_routing"],
        cost_config=infra["cost_config"],
    )

    # Use existing event summaries as review text (realistic proxy until real G2/Capterra crawls)
    batches: list[ReviewBatch] = []
    for company in COMPANIES:
        label = LABELS[company]
        evs = await infra["event_store"].get_recent_events(company=company, days=90, limit=10)
        texts = [e.get("summary","") for e in evs if e.get("summary","") and len(e["summary"]) > 40]
        if texts:
            batches.append(ReviewBatch(
                company=company,
                source_platform="g2",
                reviews=texts[:3],
                crawl_date=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            ))
            log(f"[yellow]Sentiment[/] [{label}] {len(texts[:3])} review snippet(s) queued")

    if not batches:
        log("[dim]Sentiment[/] No review text available — skipped")
        set_status("sentiment", "SKIPPED")
        return

    try:
        results = await agent.run(batches=batches, run_id=f"live_sentiment_{uuid.uuid4().hex[:8]}")
        state["metrics"]["sentiment_events"] = len(results)
        for r in results[:3]:
            log(f"[green]Sentiment[/] [{r.company}] {r.aspect} → {r.sentiment} ({r.sentiment_score:+.2f})")
        set_status("sentiment", "DONE")
        log(f"Sentiment Agent: [bold]{len(results)}[/] CustomerSentimentEvent(s)")
    except Exception as e:
        log(f"[red]Sentiment[/] error: {str(e)[:80]}")
        set_status("sentiment", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 5 — Hiring Signal Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_hiring(infra: dict) -> None:
    from agents.hiring_signal_agent import HiringSignalAgent
    set_status("hiring", "RUNNING")
    log("[yellow]HiringSignal[/] Analysing hiring_trend events → WeakSignalPrediction…")

    agent = HiringSignalAgent(
        event_store=infra["event_store"],
        model_config=infra["model_routing"],
        cost_config=infra["cost_config"],
    )

    try:
        predictions = await agent.run(companies=COMPANIES,
                                       run_id=f"live_hiring_{uuid.uuid4().hex[:8]}")
        state["metrics"]["predictions"] = len(predictions)
        state["results"]["predictions"] = predictions
        for p in predictions:
            log(f"[green]HiringSignal[/] [{LABELS.get(p.company, p.company)}] "
                f"→ [bold]{p.predicted_direction[:60]}[/]  conf={p.confidence:.2f}")
        set_status("hiring", "DONE")
        log(f"Hiring Signal Agent: [bold]{len(predictions)}[/] WeakSignalPrediction(s)")
    except Exception as e:
        log(f"[red]HiringSignal[/] error: {str(e)[:80]}")
        set_status("hiring", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 6 — Narrative Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_narrative(infra: dict) -> None:
    from agents.narrative_agent import NarrativeAgent
    set_status("narrative", "RUNNING")
    log("[yellow]Narrative[/] Clustering events → strategic story detection…")

    agent = NarrativeAgent(
        event_store=infra["event_store"], embedder=infra["embedder"],
        model_config=infra["model_routing"], cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    try:
        narratives = await agent.run(companies=COMPANIES,
                                     run_id=f"live_narrative_{uuid.uuid4().hex[:8]}")
        state["metrics"]["narratives"] = len(narratives)
        state["results"]["narratives"] = narratives
        for n in narratives:
            log(f"[green]Narrative[/] [{LABELS.get(n.company, n.company)}] "
                f"[bold]{n.narrative_title}[/]  ({len(n.constituent_event_ids)} events, conf={n.confidence:.2f})")
        set_status("narrative", "DONE")
        log(f"Narrative Agent: [bold]{len(narratives)}[/] NarrativeEvent(s)")
    except Exception as e:
        log(f"[red]Narrative[/] error: {str(e)[:80]}")
        set_status("narrative", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 7 — Convergence Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_convergence(infra: dict) -> None:
    from agents.convergence_agent import ConvergenceAgent
    set_status("convergence", "RUNNING")
    log("[yellow]Convergence[/] Cross-competitor event clustering → market trends…")

    agent = ConvergenceAgent(
        event_store=infra["event_store"], embedder=infra["embedder"],
        model_config=infra["model_routing"], cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    try:
        trends = await agent.run(companies=COMPANIES,
                                 run_id=f"live_conv_{uuid.uuid4().hex[:8]}")
        state["results"]["market_trends"] = trends
        for t in trends:
            companies_str = ", ".join(LABELS.get(c, c) for c in (t.companies_involved or []))
            log(f"[green]Convergence[/] [bold]{t.summary[:60]}[/]  "
                f"companies=[{companies_str}]  conf={t.confidence_score:.2f}")
        set_status("convergence", "DONE")
        log(f"Convergence Agent: [bold]{len(trends)}[/] MarketTrendEvent(s)")
    except Exception as e:
        log(f"[red]Convergence[/] error: {str(e)[:80]}")
        set_status("convergence", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 8 — Threat Scoring Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_threat(infra: dict) -> None:
    from agents.threat_scoring_agent import ThreatScoringAgent
    set_status("threat", "RUNNING")
    log("[yellow]ThreatScore[/] Computing velocity+type+recency → ThreatScore per company…")

    agent = ThreatScoringAgent(
        event_store=infra["event_store"],
        model_config=infra["model_routing"],
        cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    try:
        scores = await agent.run(companies=COMPANIES,
                                 run_id=f"live_threat_{uuid.uuid4().hex[:8]}")
        state["metrics"]["threat_scores"] = len(scores)
        state["results"]["threat_scores"] = scores
        for s in scores:
            tier_color = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "green"}.get(s.tier, "white")
            log(f"[green]ThreatScore[/] [{LABELS.get(s.company, s.company)}] "
                f"[{tier_color}]{s.tier}[/] {s.score:.1f}/100  trend={s.trend}")
        set_status("threat", "DONE")
        log(f"Threat Scoring: [bold]{len(scores)}[/] ThreatScore(s) stored")
    except Exception as e:
        log(f"[red]ThreatScore[/] error: {str(e)[:80]}")
        set_status("threat", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 9 — Digest Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_digest(infra: dict) -> None:
    from agents.digest_agent import DigestAgent
    set_status("digest", "RUNNING")
    log("[yellow]Digest[/] Generating weekly stakeholder briefs (sales, product, exec)…")

    agent = DigestAgent(
        event_store=infra["event_store"],
        model_config=infra["model_routing"],
        cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    try:
        digests = await agent.run(companies=COMPANIES,
                                  run_id=f"live_digest_{uuid.uuid4().hex[:8]}")
        state["metrics"]["digests"] = len(digests)
        state["results"]["digests"] = digests
        for d in digests[:3]:
            role = d.get("stakeholder_role", "?")
            words = len(d.get("content","").split())
            log(f"[green]Digest[/] [{role}] brief generated  ({words} words)")
        set_status("digest", "DONE")
        log(f"Digest Agent: [bold]{len(digests)}[/] stakeholder brief(s)")
    except Exception as e:
        log(f"[red]Digest[/] error: {str(e)[:80]}")
        set_status("digest", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 10 — Intelligence Agent
# ═════════════════════════════════════════════════════════════════════════════
async def stage_intelligence(infra: dict) -> None:
    from agents.intelligence_agent import IntelligenceAgent
    set_status("intelligence", "RUNNING")
    log("[yellow]Intelligence[/] Generating stakeholder insight: McKinsey × sales role…")

    agent = IntelligenceAgent(
        event_store=infra["event_store"], vector_store=infra["vector_store"],
        model_config=infra["model_routing"], cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    try:
        result = await agent.run(
            company="McKinsey & Company",
            stakeholder_role="sales",
            query="Key competitive threats and AI moves to watch",
            days=90,
        )
        n_insights = len(result.key_insights)
        log(f"[green]Intelligence[/] insight generated  ({n_insights} key insight(s))")
        log(f"  [dim]→ {result.summary[:100]}[/]")
        for ins in result.recommended_actions[:2]:
            log(f"  [dim]  action: {ins[:75]}[/]")
        state["results"]["intelligence"] = result
        set_status("intelligence", "DONE")
    except Exception as e:
        log(f"[red]Intelligence[/] error: {str(e)[:80]}")
        set_status("intelligence", "FAILED")


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 11 — Conversational Agent (3 queries)
# ═════════════════════════════════════════════════════════════════════════════
CONV_QUERIES = [
    ("McKinsey & Company", "What strategic moves has McKinsey made in AI recently? I'm in a sales call tomorrow.", "sales"),
    ("Boston Consulting Group", "What is BCG's threat level and what new products did they launch?", "product"),
    ("Bain & Company", "What does Bain's hiring signal tell us about where they're heading?", "exec"),
]


async def stage_conversational(infra: dict) -> None:
    from agents.conversational_agent import ConversationalAgent
    set_status("conv", "RUNNING")

    agent = ConversationalAgent(
        event_store=infra["event_store"], vector_store=infra["vector_store"],
        cache=infra["cache"], embedder=infra["embedder"],
        tavily_search=infra["tavily"],
        model_config=infra["model_routing"], cost_config=infra["cost_config"],
        llm_adapter=infra["llm"],
    )

    answers = []
    for company, query, role in CONV_QUERIES:
        label = LABELS[company]
        log(f"[yellow]Conv[/] [{label}/{role}] \"{query[:55]}…\"")
        t0 = time.monotonic()
        try:
            done = None
            async for chunk in agent.stream(
                message=query,
                session_id=f"live_{uuid.uuid4().hex[:6]}",
                stakeholder_role=role,
            ):
                if chunk.get("type") == "status":
                    log(f"  [dim]→ {chunk['content']}[/]")
                elif chunk.get("type") == "done":
                    done = chunk
                elif chunk.get("type") == "error":
                    log(f"  [red]error: {chunk.get('content','?')[:60]}[/]")

            elapsed = time.monotonic() - t0
            if done:
                answer = done.get("content","")
                sources = done.get("sources", [])
                conf    = done.get("confidence", 0) or 0
                live    = done.get("is_live_fallback", False)
                from_   = "Tavily" if live else "KB"
                log(f"[green]Conv[/] [{label}] answered in {elapsed:.1f}s  "
                    f"conf={conf:.2f}  from={from_}  sources={len(sources)}")
                log(f"  [bold]{answer[:120]}{'…' if len(answer)>120 else ''}[/]")
                answers.append(dict(company=company, role=role, answer=answer,
                                    conf=conf, from_=from_, elapsed=elapsed, sources=sources))
        except Exception as e:
            log(f"[red]Conv[/] [{label}] error: {str(e)[:70]}")

    state["results"]["conv_answers"] = answers
    set_status("conv", "DONE")
    log(f"Conversational Agent: [bold]{len(answers)}/3[/] queries answered")


# ═════════════════════════════════════════════════════════════════════════════
#  Final summary panel
# ═════════════════════════════════════════════════════════════════════════════
def print_summary() -> None:
    console.print()
    console.rule("[bold cyan]PIPELINE COMPLETE[/bold cyan]")
    m = state["metrics"]
    elapsed = time.monotonic() - state["start_ts"]

    # Agent status
    t = Table(title="Agent Results", box=box.ROUNDED, border_style="cyan")
    t.add_column("Agent", style="bold")
    t.add_column("Status")
    t.add_column("Detail")
    details = {
        "infra":        f"{m['events_total']} events in KB",
        "research":     "Tavily search ×3 companies",
        "extraction":   f"{m['events_new']} new event(s) stored",
        "matrix":       f"{m['matrix_updates']} matrix update(s)",
        "sentiment":    f"{m['sentiment_events']} CustomerSentimentEvent(s)",
        "hiring":       f"{m['predictions']} WeakSignalPrediction(s)",
        "narrative":    f"{m['narratives']} NarrativeEvent(s)",
        "convergence":  f"{len(state['results'].get('market_trends',[]))} MarketTrendEvent(s)",
        "threat":       f"{m['threat_scores']} ThreatScore(s)",
        "digest":       f"{m['digests']} stakeholder brief(s)",
        "intelligence": "1 InsightOutput (McKinsey×sales)" if state["results"].get("intelligence") else "0",
        "conv":         f"{len(state['results'].get('conv_answers',[]))} answered",
    }
    for key, label in AGENTS:
        st = state["agent_status"][key]
        style, icon = STATUS_STYLE[st]
        t.add_row(label, Text(f"{icon} {st}", style=style), details.get(key,""))
    console.print(t)

    # KB metrics
    console.print()
    m2 = Table(title="KB Metrics", box=box.ROUNDED, border_style="blue")
    m2.add_column("Metric", style="bold")
    m2.add_column("Value",  style="bold cyan")
    for k, v in [
        ("Events in KB", m["events_total"]), ("New this run", m["events_new"]),
        ("Narratives detected", m["narratives"]), ("Threat scores", m["threat_scores"]),
        ("Weak signal predictions", m["predictions"]),
        ("Sentiment events", m["sentiment_events"]),
        ("LLM cost", f"${m['cost_usd']:.4f}"),
        ("Total elapsed", f"{elapsed:.0f}s"),
    ]:
        m2.add_row(str(k), str(v))
    console.print(m2)

    # Conversational answers
    answers = state["results"].get("conv_answers", [])
    if answers:
        console.print()
        console.rule("[bold]Conversational Answers[/bold]")
        for a in answers:
            label = LABELS.get(a["company"], a["company"])
            console.print(f"\n[bold cyan][{label} / {a['role']}][/]  "
                          f"[dim]from={a['from_']}  conf={a['conf']:.2f}  {a['elapsed']:.1f}s[/]")
            console.print(f"  {a['answer'][:300]}{'…' if len(a['answer'])>300 else ''}")
            if a["sources"]:
                for src in a["sources"][:2]:
                    url = src.get("url", src) if isinstance(src, dict) else str(src)
                    console.print(f"  [dim]  → {url[:85]}[/]")

    # Threat scores
    scores = state["results"].get("threat_scores", [])
    if scores:
        console.print()
        console.rule("[bold]Threat Scores[/bold]")
        for s in scores:
            tier_color = {"HIGH":"red","MEDIUM":"yellow","LOW":"green"}.get(s.tier,"white")
            console.print(f"  [{tier_color}]{s.tier}[/]  {LABELS.get(s.company,s.company):<12}  "
                          f"{s.score:.1f}/100  trend={s.trend}")

    # Narratives
    narratives = state["results"].get("narratives", [])
    if narratives:
        console.print()
        console.rule("[bold]Narratives Detected[/bold]")
        for n in narratives:
            console.print(f"  [cyan]{LABELS.get(n.company,n.company):<12}[/]  "
                          f"[bold]{n.narrative_title}[/]  "
                          f"({len(n.constituent_event_ids)} events, conf={n.confidence:.2f})")

    console.print()


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════
async def run_pipeline(layout: Layout, live: Live) -> None:
    infra = await wire()
    update_layout(layout); live.refresh()

    stages = [
        ("infra",        stage_infra),
        ("research",     stage_research_extraction),
        ("matrix",       stage_matrix),
        ("sentiment",    stage_sentiment),
        ("hiring",       stage_hiring),
        ("narrative",    stage_narrative),
        ("convergence",  stage_convergence),
        ("threat",       stage_threat),
        ("digest",       stage_digest),
        ("intelligence", stage_intelligence),
        ("conv",         stage_conversational),
    ]

    for key, fn in stages:
        try:
            if key in ("research",):  # extraction is called inside research
                await fn(infra)
            else:
                await fn(infra)
        except Exception as e:
            if state["agent_status"].get(key) == "RUNNING":
                set_status(key, "FAILED")
            log(f"[red]Stage {key} crashed:[/] {str(e)[:80]}")
        update_layout(layout); live.refresh()
        await asyncio.sleep(0.05)

    await infra["event_store"].disconnect()
    await infra["cache"].disconnect()


async def main() -> None:
    layout = make_layout()
    update_layout(layout)

    with Live(layout, console=console, refresh_per_second=4, screen=False) as live:
        await run_pipeline(layout, live)
        update_layout(layout); live.refresh()
        await asyncio.sleep(0.5)

    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
