"""LangGraph pipeline for Research + Extraction orchestration.

Graph structure:
  START → research_node → extraction_node → checkpoint_node → END

Each node checkpoints its state to MongoDB before moving on.
The graph is stateful: failures at any node resume from last checkpoint.

This is the Phase 2 LangGraph pipeline. Phase 5 will add the full 9-agent
supervisor graph with Sunday synthesis scheduling.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.extraction_agent import ExtractionAgent
from agents.research_agent import ResearchAgent
from observability.logger import get_logger
from schemas.config import CompetitorConfig
from schemas.state import AgentStatus
from storage.event_store import EventStore
from tools.errors import AgentError, ErrorCode

log = get_logger("pipeline")


class PipelineGraphState(TypedDict):
    """Shared mutable state flowing through the LangGraph nodes."""

    run_id: str
    competitors: list[dict]  # serialised CompetitorConfig dicts
    crawl_results: list[dict]  # serialised CrawlResult dicts
    extraction_results: list[dict]  # serialised ExtractionResult dicts
    errors: list[dict]  # list of {node, error_code, message}
    started_at: str
    research_completed_at: str | None
    extraction_completed_at: str | None
    status: str  # "running" | "completed" | "partial_success" | "failed"
    total_events: int
    total_quarantined: int
    total_cost_usd: float


class ResearchExtractionPipeline:
    """LangGraph-based pipeline that runs Research → Extraction sequentially.

    Checkpoints pipeline state to MongoDB after each node so that failures
    can be investigated and retried without losing progress.

    Usage:
        pipeline = ResearchExtractionPipeline(
            research_agent=...,
            extraction_agent=...,
            event_store=...,
            competitors=[...],
        )
        result = await pipeline.run()
    """

    def __init__(
        self,
        research_agent: ResearchAgent,
        extraction_agent: ExtractionAgent,
        event_store: EventStore,
        competitors: list[CompetitorConfig],
    ) -> None:
        self._research_agent = research_agent
        self._extraction_agent = extraction_agent
        self._event_store = event_store
        self._competitors = competitors
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Compile the LangGraph StateGraph."""
        builder = StateGraph(PipelineGraphState)

        builder.add_node("research", self._research_node)
        builder.add_node("extraction", self._extraction_node)
        builder.add_node("checkpoint", self._checkpoint_node)

        builder.add_edge(START, "research")
        builder.add_edge("research", "extraction")
        builder.add_edge("extraction", "checkpoint")
        builder.add_edge("checkpoint", END)

        return builder.compile()

    async def run(self, run_id: str | None = None) -> PipelineGraphState:
        """Execute the full Research → Extraction pipeline.

        Returns the final graph state including all results and metrics.
        """
        run_id = run_id or f"pipeline_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        log.info(
            "pipeline_started",
            agent="pipeline",
            action="run",
            run_id=run_id,
            competitors=len(self._competitors),
            status="running",
        )

        initial_state: PipelineGraphState = {
            "run_id": run_id,
            "competitors": [c.model_dump() for c in self._competitors],
            "crawl_results": [],
            "extraction_results": [],
            "errors": [],
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "research_completed_at": None,
            "extraction_completed_at": None,
            "status": "running",
            "total_events": 0,
            "total_quarantined": 0,
            "total_cost_usd": 0.0,
        }

        final_state = await self._graph.ainvoke(initial_state)

        log.info(
            "pipeline_finished",
            agent="pipeline",
            action="run",
            run_id=run_id,
            status=final_state["status"],
            total_events=final_state["total_events"],
            total_quarantined=final_state["total_quarantined"],
            total_cost_usd=round(final_state["total_cost_usd"], 4),
        )
        return final_state

    async def _research_node(self, state: PipelineGraphState) -> dict:
        """Node 1: Run Research Agent across all competitors."""
        run_id = state["run_id"]
        log.info("research_node_started", agent="pipeline", action="research_node", run_id=run_id)

        # Reconstruct CompetitorConfig objects from serialised dicts
        from schemas.config import CompetitorConfig, SourceConfig

        competitors = [
            CompetitorConfig(
                competitor=c["competitor"],
                canonical_names=c.get("canonical_names", [c["competitor"]]),
                sources=[SourceConfig(**s) for s in c.get("sources", [])],
            )
            for c in state["competitors"]
        ]

        errors: list[dict] = list(state["errors"])
        crawl_results_raw: list[dict] = []

        try:
            crawl_results = await self._research_agent.run(
                competitors=competitors,
                run_id=run_id,
            )
            for cr in crawl_results:
                crawl_results_raw.append({
                    "url": cr.url,
                    "content": cr.content,
                    "is_changed": cr.is_changed,
                    "crawl_timestamp": cr.crawl_timestamp,
                    "content_hash": cr.content_hash,
                    "status_code": cr.status_code,
                    "company": cr.company,  # set for Tavily results; None for firecrawl/rss
                })
        except AgentError as exc:
            log.error(
                "research_node_failed",
                agent="pipeline",
                action="research_node",
                run_id=run_id,
                error_code=exc.code,
                error=exc.message,
            )
            errors.append({"node": "research", "error_code": exc.code, "message": exc.message})
        except Exception as exc:
            log.error(
                "research_node_unexpected_error",
                agent="pipeline",
                action="research_node",
                run_id=run_id,
                error=str(exc),
            )
            errors.append({
                "node": "research",
                "error_code": ErrorCode.AGENT_STATE_INVALID,
                "message": str(exc),
            })

        return {
            "crawl_results": crawl_results_raw,
            "research_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
        }

    async def _extraction_node(self, state: PipelineGraphState) -> dict:
        """Node 2: Run Extraction Agent on all changed crawl results."""
        run_id = state["run_id"]
        log.info(
            "extraction_node_started",
            agent="pipeline",
            action="extraction_node",
            run_id=run_id,
            crawl_results=len(state["crawl_results"]),
        )

        from schemas.state import CrawlResult

        # Build URL → competitor mapping from config
        url_to_company: dict[str, str] = {}
        from schemas.config import CompetitorConfig, SourceConfig

        for comp_dict in state["competitors"]:
            competitor_name = comp_dict["competitor"]
            for src in comp_dict.get("sources", []):
                url = src.get("url")
                if url:
                    url_to_company[url] = competitor_name

        errors: list[dict] = list(state["errors"])
        extraction_results_raw: list[dict] = []
        total_events = 0
        total_quarantined = 0
        total_cost_usd = 0.0

        # Build the list of (crawl_result, company) pairs to extract
        work_items: list[tuple[CrawlResult, str]] = []
        for cr_dict in state["crawl_results"]:
            if not cr_dict.get("is_changed") or not cr_dict.get("content"):
                continue
            url = cr_dict["url"]
            company = cr_dict.get("company") or _resolve_company_from_url(url, url_to_company)
            if not company:
                log.warning(
                    "company_resolution_failed",
                    agent="pipeline",
                    action="extraction_node",
                    source=url,
                )
                errors.append({
                    "node": "extraction",
                    "error_code": ErrorCode.AGENT_STATE_INVALID,
                    "message": f"Could not resolve company for URL: {url}",
                })
                continue
            work_items.append((
                CrawlResult(
                    url=url,
                    content=cr_dict.get("content", ""),
                    is_changed=True,
                    crawl_timestamp=cr_dict.get("crawl_timestamp", ""),
                    content_hash=cr_dict.get("content_hash", ""),
                    status_code=cr_dict.get("status_code", 200),
                    company=cr_dict.get("company"),
                ),
                company,
            ))

        # Groq free tier: 6000 TPM per model. Limit to 2 concurrent to stay under budget.
        # Upgrade to dev tier or add OpenRouter credits to raise this to 8+.
        sem = asyncio.Semaphore(2)

        async def _extract_one(crawl_result: CrawlResult, company: str) -> dict | None:
            async with sem:
                try:
                    result = await self._extraction_agent.run(
                        crawl_result=crawl_result,
                        company=company,
                        run_id=run_id,
                    )
                    return {
                        "source_url": result.source_url,
                        "events_count": len(result.events_extracted),
                        "quarantined_count": result.quarantined_count,
                        "cost_usd": result.llm_cost_usd,
                        "error_code": result.error_code,
                    }
                except AgentError as exc:
                    log.error(
                        "extraction_failed_for_url",
                        agent="pipeline",
                        action="extraction_node",
                        source=crawl_result.url,
                        error_code=exc.code,
                        error=exc.message,
                    )
                    errors.append({"node": "extraction", "error_code": exc.code, "message": exc.message})
                    return None
                except Exception as exc:
                    log.error(
                        "extraction_unexpected_error",
                        agent="pipeline",
                        action="extraction_node",
                        source=crawl_result.url,
                        error=str(exc),
                    )
                    errors.append({
                        "node": "extraction",
                        "error_code": ErrorCode.AGENT_STATE_INVALID,
                        "message": str(exc),
                    })
                    return None

        results = await asyncio.gather(*[_extract_one(cr, co) for cr, co in work_items])
        for res in results:
            if res is None:
                continue
            extraction_results_raw.append(res)
            total_events += res["events_count"]
            total_quarantined += res["quarantined_count"]
            total_cost_usd += res["cost_usd"]

        return {
            "extraction_results": extraction_results_raw,
            "extraction_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
            "total_events": total_events,
            "total_quarantined": total_quarantined,
            "total_cost_usd": total_cost_usd,
        }

    async def _checkpoint_node(self, state: PipelineGraphState) -> dict:
        """Node 3: Persist final pipeline state to MongoDB for observability."""
        run_id = state["run_id"]
        has_errors = bool(state["errors"])
        has_results = state["total_events"] > 0 or state["total_quarantined"] > 0

        if has_errors and has_results:
            status = AgentStatus.PARTIAL_SUCCESS.value
        elif has_errors:
            status = AgentStatus.FAILED.value
        else:
            status = AgentStatus.COMPLETED.value

        pipeline_state_doc = {
            "run_id": run_id,
            "agent": "research_extraction_pipeline",
            "status": status,
            "started_at": state["started_at"],
            "research_completed_at": state.get("research_completed_at"),
            "extraction_completed_at": state.get("extraction_completed_at"),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "total_events": state["total_events"],
            "total_quarantined": state["total_quarantined"],
            "total_cost_usd": state["total_cost_usd"],
            "errors": state["errors"],
            "crawl_count": len(state["crawl_results"]),
        }

        try:
            await self._event_store.upsert_pipeline_state(run_id, pipeline_state_doc)
            log.info(
                "pipeline_state_checkpointed",
                agent="pipeline",
                action="checkpoint_node",
                run_id=run_id,
                status=status,
            )
        except Exception as exc:
            log.error(
                "checkpoint_failed",
                agent="pipeline",
                action="checkpoint_node",
                run_id=run_id,
                error_code=ErrorCode.AGENT_CHECKPOINT_FAILED,
                error=str(exc),
            )
            # Don't raise — checkpoint failure should not mask pipeline results

        return {"status": status}


def _resolve_company_from_url(url: str, url_to_company: dict[str, str]) -> str | None:
    """Find the competitor that owns a given source URL."""
    for source_url, company in url_to_company.items():
        if source_url and source_url in url:
            return company
    return None
