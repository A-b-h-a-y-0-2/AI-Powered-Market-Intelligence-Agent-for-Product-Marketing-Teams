"""Application entry point — all 9 agents wired and scheduled.

Schedule:
  Every 30min  →  RSS Crawler (poll RSS feeds)
  Daily 02:00  →  Daily pipeline: Research → Extraction → Matrix (event-triggered)
  Daily 05:00  →  Sentiment Agent (G2/Reddit/Capterra reviews)
  Sunday 03:00 →  Sunday synthesis supervisor:
                  Hiring Signal → Narrative → Convergence → Threat Scoring → Digest
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from agents.convergence_agent import ConvergenceAgent
from agents.discovery_agent import DiscoveryAgent
from agents.digest_agent import DigestAgent
from agents.extraction_agent import ExtractionAgent
from agents.hiring_signal_agent import HiringSignalAgent
from agents.intelligence_agent import IntelligenceAgent
from agents.matrix_agent import MatrixAgent
from agents.narrative_agent import NarrativeAgent
from agents.research_agent import ResearchAgent
from agents.sentiment_agent import SentimentAgent, ReviewBatch
from agents.threat_scoring_agent import ThreatScoringAgent
from agents.conversational_agent import ConversationalAgent
from api.app import create_app
from observability.logger import configure_logging, get_logger
from observability.tracing import init_tracing
from pipeline.research_extraction_graph import ResearchExtractionPipeline
from pipeline.supervisor import SundaySupervisor
from schemas.config import AppConfig, CompetitorConfig, SourceConfig
from storage.cache import CacheStore
from storage.event_store import EventStore
from storage.graph_store import GraphStore
from storage.vector_store import VectorStore
from tools.apify import ApifyClient
from tools.crawler import CircuitBreaker, Crawler
from tools.embedder import Embedder
from tools.errors import ConfigError, ErrorCode
from tools.llm_adapter import LLMAdapter
from tools.rss_crawler import RSSCrawler
from tools.search import TavilySearch

load_dotenv()


def load_config() -> AppConfig:
    try:
        return AppConfig()
    except Exception as exc:
        print(f"[CONFIG_INVALID] Failed to load configuration: {exc}", file=sys.stderr)
        sys.exit(1)


def load_yaml(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            code=ErrorCode.CONFIG_MISSING_FIELD,
            message=f"Config file not found: {path}",
        )
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_competitors(sources_path: str = "config/sources.yaml") -> list[CompetitorConfig]:
    data = load_yaml(sources_path)
    competitors = []
    for entry in data.get("competitors", []):
        sources = [SourceConfig(**s) for s in entry.get("sources", [])]
        competitor = CompetitorConfig(
            competitor=entry["competitor"],
            canonical_names=entry.get("canonical_names", [entry["competitor"]]),
            sources=sources,
        )
        competitors.append(competitor)
    return competitors


def load_model_config(models_path: str = "config/models.yaml") -> dict:
    data = load_yaml(models_path)
    return {
        "routing": data.get("routing", {}),
        "costs": data.get("costs", {}),
        "retry": data.get("retry", {}),
    }


# ── Scheduled pipeline jobs ───────────────────────────────────────────────────

async def run_daily_pipeline(pipeline: ResearchExtractionPipeline, log) -> None:
    """Daily Research + Extraction via LangGraph pipeline (02:00 UTC)."""
    try:
        state = await pipeline.run()
        log.info(
            "daily_pipeline_completed",
            action="daily_pipeline",
            status=state["status"],
            total_events=state["total_events"],
            total_cost_usd=round(state["total_cost_usd"], 4),
        )
    except Exception as exc:
        log.error("daily_pipeline_failed", action="daily_pipeline", error=str(exc))
        raise


async def run_rss_poll(
    rss_crawler: RSSCrawler,
    extraction_agent: ExtractionAgent,
    competitors: list[CompetitorConfig],
    log,
) -> None:
    """30-minute RSS poll for all tracked competitors."""
    from schemas.state import CrawlResult

    since = (datetime.now(tz=timezone.utc) - timedelta(minutes=35)).isoformat()
    run_id = f"rss_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    feeds = [
        {"url": src.url, "company": comp.competitor}
        for comp in competitors
        for src in comp.sources
        if src.type == "rss" and src.url
    ]
    if not feeds:
        return

    feed_results = await rss_crawler.fetch_multiple(feeds, since_timestamp=since)
    for feed_result in feed_results:
        company = next(
            (f["company"] for f in feeds if f["url"] == feed_result.feed_url), None
        )
        if not company:
            continue
        for entry in feed_result.entries:
            crawl_result = CrawlResult(
                url=entry.url,
                content=f"{entry.title}\n\n{entry.content}",
                is_changed=True,
                crawl_timestamp=entry.published_at,
                content_hash=entry.entry_hash,
            )
            try:
                await extraction_agent.run(
                    crawl_result=crawl_result, company=company, run_id=run_id
                )
            except Exception as exc:
                log.error(
                    "rss_extraction_failed",
                    action="rss_poll",
                    source=entry.url,
                    company=company,
                    error=str(exc),
                )


async def run_sentiment_pipeline(
    sentiment_agent: SentimentAgent,
    event_store: EventStore,
    competitors: list[CompetitorConfig],
    log,
) -> None:
    """Daily 05:00 UTC — run ABSA on crawled review content."""
    run_id = f"sentiment_{datetime.now(tz=timezone.utc).strftime('%Y%m%d')}"
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    batches: list[ReviewBatch] = []
    for comp in competitors:
        # Pull raw review content from event store (crawled by Research Agent)
        reviews = await event_store.get_recent_events(
            company=comp.competitor,
            days=1,
            event_types=["customer_sentiment"],
            min_confidence=0.0,
            limit=200,
        )
        raw_texts = [r.get("summary", "") for r in reviews if r.get("summary")]
        if raw_texts:
            for platform in ("g2", "capterra", "reddit"):
                platform_reviews = [t for t in raw_texts if platform in t.lower()]
                if platform_reviews:
                    batches.append(ReviewBatch(
                        company=comp.competitor,
                        source_platform=platform,
                        reviews=platform_reviews,
                        crawl_date=today,
                    ))

    if batches:
        await sentiment_agent.run(batches=batches, run_id=run_id)
    else:
        log.info("no_review_batches", action="sentiment_pipeline", status="skip")


async def run_sunday_supervisor(supervisor: SundaySupervisor, log) -> None:
    """Sunday 03:00 UTC — full synthesis: Hiring → Narrative → Convergence → Threat → Digest."""
    try:
        state = await supervisor.run()
        log.info(
            "sunday_supervisor_completed",
            action="sunday_supervisor",
            status=state["status"],
            errors=len(state["errors"]),
        )
    except Exception as exc:
        log.error("sunday_supervisor_failed", action="sunday_supervisor", error=str(exc))
        raise


# ── Startup ───────────────────────────────────────────────────────────────────

async def startup() -> tuple:
    """Initialise all services, wire all 9 agents, start scheduler."""
    config = load_config()
    configure_logging(config.log_level)
    log = get_logger("main")

    log.info("startup_started", action="startup", env=config.env)

    init_tracing(
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key,
        host=config.langfuse_host,
        enabled=config.env != "test",
    )

    competitors = load_competitors()
    company_names = [c.competitor for c in competitors]
    model_cfg = load_model_config()
    model_routing = model_cfg["routing"]
    cost_config = model_cfg["costs"]

    log.info("config_loaded", action="startup", competitors=company_names)

    # Storage
    cache = CacheStore(redis_url=config.redis_url)
    await cache.connect()

    event_store = EventStore(mongodb_uri=config.mongodb_uri, db_name=config.mongodb_db_name)
    await event_store.connect()

    vector_store = VectorStore(supabase_url=config.supabase_url, supabase_key=config.supabase_service_key)
    await vector_store.connect()

    graph_store = GraphStore(mongodb_uri=config.mongodb_uri, db_name=config.mongodb_db_name)
    await graph_store.connect()

    log.info("storage_connected", action="startup", status="ok")

    # Tools
    llm_adapter = LLMAdapter(
        groq_api_key=config.groq_api_key,
        openrouter_api_key=config.openrouter_api_key,
    )
    _embed_base_url = os.environ.get("EMBEDDING_BASE_URL", "").strip()
    if config.openai_api_key:
        # Use OpenAI directly (or any compat endpoint via EMBEDDING_BASE_URL)
        embedder = Embedder(api_key=config.openai_api_key, base_url=_embed_base_url or None)
        log.info("embedder_provider", action="startup", provider="openai", dims=1536)
    elif config.openai_api_key is None and _embed_base_url:
        # Explicit custom embedding endpoint configured
        embedder = Embedder(
            api_key=config.openrouter_api_key,
            base_url=_embed_base_url,
            model="openai/text-embedding-3-small",
        )
        log.info("embedder_provider", action="startup", provider="custom", base_url=_embed_base_url, dims=1536)
    else:
        # Local fastembed fallback — no API key needed, 384-dim bge-small-en-v1.5
        embedder = Embedder(api_key="")
        log.info("embedder_provider", action="startup", provider="local_fastembed", dims=384)
    circuit_breaker = CircuitBreaker(cache=cache)
    crawler = Crawler(firecrawl_api_key=config.firecrawl_api_key or "", cache=cache, circuit_breaker=circuit_breaker)
    rss_crawler = RSSCrawler()
    await rss_crawler.connect()

    if config.tavily_api_key:
        tavily_search: TavilySearch | None = TavilySearch(api_key=config.tavily_api_key)
        await tavily_search.connect()
        log.info("tavily_connected", action="startup", mode="research+advanced")
    else:
        tavily_search = None
        log.warning("tavily_disabled", action="startup", reason="TAVILY_API_KEY not set — live search fallback disabled")

    if config.apify_api_key:
        apify_client: ApifyClient | None = ApifyClient(api_token=config.apify_api_key)
        log.info("apify_connected", action="startup", status="ok")
    else:
        apify_client = None
        log.warning("apify_disabled", action="startup", reason="APIFY_API_KEY not set — LinkedIn/Indeed/Glassdoor/G2/Capterra scraping disabled")

    # ── All 9 agents + Intelligence + Conversational ───────────────────────────

    discovery_agent = DiscoveryAgent(cache=cache, tavily_search=tavily_search)

    research_agent = ResearchAgent(
        crawler=crawler, event_store=event_store, cache=cache, circuit_breaker=circuit_breaker,
        tavily_search=tavily_search,
        apify_client=apify_client,
    )
    extraction_agent = ExtractionAgent(
        event_store=event_store, vector_store=vector_store, cache=cache,
        embedder=embedder, model_config=model_routing, cost_config=cost_config,
        llm_adapter=llm_adapter,
    )
    matrix_agent = MatrixAgent(
        event_store=event_store, cache=cache, model_config=model_routing, cost_config=cost_config
    )
    sentiment_agent = SentimentAgent(
        event_store=event_store, model_config=model_routing, cost_config=cost_config
    )
    hiring_signal_agent = HiringSignalAgent(
        event_store=event_store, model_config=model_routing, cost_config=cost_config
    )
    narrative_agent = NarrativeAgent(
        event_store=event_store, embedder=embedder, model_config=model_routing,
        cost_config=cost_config, llm_adapter=llm_adapter,
    )
    convergence_agent = ConvergenceAgent(
        event_store=event_store, embedder=embedder, model_config=model_routing,
        cost_config=cost_config, llm_adapter=llm_adapter,
    )
    threat_scoring_agent = ThreatScoringAgent(
        event_store=event_store, model_config=model_routing, cost_config=cost_config,
        llm_adapter=llm_adapter,
    )
    digest_agent = DigestAgent(
        event_store=event_store, model_config=model_routing, cost_config=cost_config,
        llm_adapter=llm_adapter,
    )
    intelligence_agent = IntelligenceAgent(
        event_store=event_store, vector_store=vector_store,
        model_config=model_routing, cost_config=cost_config,
        llm_adapter=llm_adapter,
    )
    conversational_agent = ConversationalAgent(
        event_store=event_store, vector_store=vector_store, cache=cache,
        embedder=embedder, tavily_search=tavily_search,
        model_config=model_routing, cost_config=cost_config,
        llm_adapter=llm_adapter,
    )

    log.info("agents_initialised", action="startup", count=11)

    # LangGraph pipelines
    daily_pipeline = ResearchExtractionPipeline(
        research_agent=research_agent, extraction_agent=extraction_agent,
        event_store=event_store, competitors=competitors,
    )
    sunday_supervisor = SundaySupervisor(
        hiring_signal_agent=hiring_signal_agent,
        narrative_agent=narrative_agent,
        convergence_agent=convergence_agent,
        threat_scoring_agent=threat_scoring_agent,
        digest_agent=digest_agent,
        event_store=event_store,
        companies=company_names,
    )

    # Health checks
    for agent in [research_agent, extraction_agent, matrix_agent, intelligence_agent, conversational_agent]:
        health = await agent.health_check()
        log.info("agent_health_check", agent=health["agent"], status=health["status"])

    # ── APScheduler ────────────────────────────────────────────────────────────
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        func=run_rss_poll,
        trigger=IntervalTrigger(minutes=30),
        args=[rss_crawler, extraction_agent, competitors, log],
        id="rss_poll",
        name="RSS 30-Min Poll",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        func=run_daily_pipeline,
        trigger=CronTrigger(hour=2, minute=0),
        args=[daily_pipeline, log],
        id="daily_pipeline",
        name="Daily Research + Extraction",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        func=run_sentiment_pipeline,
        trigger=CronTrigger(hour=5, minute=0),
        args=[sentiment_agent, event_store, competitors, log],
        id="sentiment_pipeline",
        name="Daily Sentiment ABSA",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        func=run_sunday_supervisor,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
        args=[sunday_supervisor, log],
        id="sunday_supervisor",
        name="Sunday Synthesis Pipeline",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    scheduler.start()
    log.info("scheduler_started", action="startup", jobs=[j.id for j in scheduler.get_jobs()])

    # FastAPI app with all injected deps
    app = create_app(
        event_store=event_store,
        vector_store=vector_store,
        cache=cache,
        conversational_agent=conversational_agent,
        daily_pipeline=daily_pipeline,
        rss_crawler=rss_crawler,
        tavily_search=tavily_search,
        extraction_agent=extraction_agent,
        competitors=competitors,
        discovery_agent=discovery_agent,
    )

    # Expose /metrics endpoint
    from fastapi.responses import Response
    from observability.metrics import metrics

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(
            content=metrics.generate_latest(),
            media_type=metrics.content_type,
        )

    log.info("startup_complete", action="startup", env=config.env, status="ready")
    return (
        scheduler, cache, event_store, vector_store, graph_store,
        rss_crawler, tavily_search, app, competitors, company_names
    )


async def main() -> None:
    """Run the full market intelligence platform."""
    result = await startup()
    (
        scheduler, cache, event_store, vector_store, graph_store,
        rss_crawler, tavily_search, app, competitors, company_names
    ) = result
    log = get_logger("main")
    config = load_config()

    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=8000,
            loop="none",
            log_level=config.log_level.lower(),
        )
    )
    try:
        await server.serve()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)
        await cache.disconnect()
        await event_store.disconnect()
        await rss_crawler.disconnect()
        if tavily_search is not None:
            await tavily_search.disconnect()
        log.info("shutdown_complete", action="shutdown")


if __name__ == "__main__":
    asyncio.run(main())
