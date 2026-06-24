"""FastAPI route definitions for all phases.

Phase 2: /health, /admin/quarantine, /pipeline/status
Phase 3: /events, /threats, /matrix, /chat (SSE)
All later phases extend this file.

Design rules:
- All request/response types are Pydantic models (no dict[str, Any] in signatures)
- Every error path returns a typed ErrorResponse
- Database access is injected via FastAPI dependency injection (Depends)
- SSE streaming uses async generators
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from observability.logger import get_logger
from storage.event_store import EventStore
from tools.errors import ErrorCode, StorageError

log = get_logger("api")

router = APIRouter()


# ── Shared response models ─────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error_code: str
    message: str
    request_id: str


class HealthStatus(BaseModel):
    status: str
    timestamp: str
    components: dict[str, str]
    version: str = "1.0"


# ── Dependency injection ───────────────────────────────────────────────────────

def get_event_store(request: Request) -> EventStore:
    """Extract EventStore from app state (set during startup)."""
    store: EventStore | None = getattr(request.app.state, "event_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not initialised")
    return store


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthStatus, tags=["ops"])
async def health_check(request: Request) -> HealthStatus:
    """System health check. Returns component status for monitoring."""
    components: dict[str, str] = {}

    event_store: EventStore | None = getattr(request.app.state, "event_store", None)
    if event_store:
        components["event_store"] = "ok" if await event_store.health_check() else "degraded"
    else:
        components["event_store"] = "not_initialised"

    overall = "ok" if all(v == "ok" for v in components.values()) else "degraded"
    return HealthStatus(
        status=overall,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        components=components,
    )


# ── Pipeline status ────────────────────────────────────────────────────────────

class PipelineRunSummary(BaseModel):
    run_id: str
    status: str
    started_at: str | None
    completed_at: str | None
    total_events: int
    total_quarantined: int
    total_cost_usd: float
    errors: list[dict[str, str]]


class PipelineHealthSummary(BaseModel):
    last_research_run: str | None
    last_extraction_run: str | None
    last_sentiment_run: str | None
    last_narrative_run: str | None
    last_threat_run: str | None
    next_scheduled_run: str | None
    events_ingested_today: int
    events_ingested_total: int
    pipeline_health: str


@router.get("/pipeline/summary", response_model=PipelineHealthSummary, tags=["ops"])
async def get_pipeline_summary(
    event_store: EventStore = Depends(get_event_store),
) -> PipelineHealthSummary:
    """Return aggregate pipeline health stats inferred from stored events."""
    from datetime import timedelta

    db = event_store._require_db()  # type: ignore[attr-defined]
    now = datetime.now(tz=timezone.utc)
    today_cutoff = (now - timedelta(hours=24)).isoformat()

    async def last_event_ts(event_type: str) -> str | None:
        doc = await db["events"].find_one(
            {"event_type": event_type}, sort=[("timestamp", -1)]
        )
        return doc.get("timestamp") if doc else None

    narrative_ts = await last_event_ts("narrative")
    threat_ts = await last_event_ts("threat_score")
    sentiment_ts = await last_event_ts("customer_sentiment")
    hiring_ts = await last_event_ts("hiring_trend")

    # Use hiring_trend timestamp as proxy for research+extraction run
    research_ts = hiring_ts or await last_event_ts("feature_launch") or await last_event_ts("partnership")

    today_count = await db["events"].count_documents({"timestamp": {"$gte": today_cutoff}})
    total_count = await db["events"].count_documents({})

    health = "healthy" if total_count > 0 else "down"

    return PipelineHealthSummary(
        last_research_run=research_ts,
        last_extraction_run=research_ts,
        last_sentiment_run=sentiment_ts,
        last_narrative_run=narrative_ts,
        last_threat_run=threat_ts,
        next_scheduled_run=None,
        events_ingested_today=today_count,
        events_ingested_total=total_count,
        pipeline_health=health,
    )


@router.get("/pipeline/status/{run_id}", response_model=PipelineRunSummary, tags=["ops"])
async def get_pipeline_status(
    run_id: str,
    event_store: EventStore = Depends(get_event_store),
) -> PipelineRunSummary:
    """Get the status of a specific pipeline run by run_id."""
    try:
        state = await event_store.get_pipeline_state(run_id)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    if not state:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")

    return PipelineRunSummary(
        run_id=state["run_id"],
        status=state.get("status", "unknown"),
        started_at=state.get("started_at"),
        completed_at=state.get("completed_at"),
        total_events=state.get("total_events", 0),
        total_quarantined=state.get("total_quarantined", 0),
        total_cost_usd=state.get("total_cost_usd", 0.0),
        errors=state.get("errors", []),
    )


@router.get("/pipeline/status", response_model=list[PipelineRunSummary], tags=["ops"])
async def list_pipeline_runs(
    limit: int = Query(default=10, ge=1, le=100),
    event_store: EventStore = Depends(get_event_store),
) -> list[PipelineRunSummary]:
    """List recent pipeline runs."""
    try:
        states = await event_store.get_recent_pipeline_states(limit=limit)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    return [
        PipelineRunSummary(
            run_id=s["run_id"],
            status=s.get("status", "unknown"),
            started_at=s.get("started_at"),
            completed_at=s.get("completed_at"),
            total_events=s.get("total_events", 0),
            total_quarantined=s.get("total_quarantined", 0),
            total_cost_usd=s.get("total_cost_usd", 0.0),
            errors=s.get("errors", []),
        )
        for s in states
    ]


class PipelineTriggerRequest(BaseModel):
    mode: str = Field(
        default="full",
        description="'full' = research + extraction | 'rss' = RSS poll only | 'tavily' = Tavily search only",
    )
    companies: list[str] | None = Field(
        default=None,
        description="Subset of tracked companies to run. None = all.",
    )


class PipelineTriggerResponse(BaseModel):
    run_id: str
    mode: str
    companies: list[str]
    message: str


@router.post("/pipeline/trigger", response_model=PipelineTriggerResponse, tags=["ops"])
async def trigger_pipeline(
    body: PipelineTriggerRequest,
    request: Request,
) -> PipelineTriggerResponse:
    """Manually trigger the research + extraction pipeline.

    Runs asynchronously in the background. Poll GET /api/v1/pipeline/status/{run_id}
    to track progress.

    Modes:
    - full: research (Firecrawl + RSS) + extraction (LLM event extraction)
    - rss: RSS feeds only (fast, ~30 seconds)
    - tavily: Tavily search sources only (medium, ~60 seconds)
    """
    import asyncio
    import uuid as _uuid

    pipeline = getattr(request.app.state, "daily_pipeline", None)
    rss_crawler = getattr(request.app.state, "rss_crawler", None)
    competitors_cfg = getattr(request.app.state, "competitors", [])

    if not pipeline and body.mode == "full":
        raise HTTPException(
            status_code=503,
            detail="Pipeline not initialised. Server may still be starting up.",
        )

    run_id = f"manual_{_uuid.uuid4().hex[:8]}"

    # Filter companies if specified
    if body.companies:
        filtered = [c for c in competitors_cfg if c.competitor in body.companies]
    else:
        filtered = list(competitors_cfg)

    company_names = [c.competitor for c in filtered]

    async def _run_full():
        try:
            state = await pipeline.run(run_id=run_id)
            log.info(
                "manual_pipeline_completed",
                agent="api",
                action="trigger_pipeline",
                run_id=run_id,
                status=state.get("status"),
                total_events=state.get("total_events", 0),
            )
        except Exception as exc:
            log.error("manual_pipeline_failed", agent="api", run_id=run_id, error=str(exc))

    async def _run_rss():
        from datetime import datetime, timedelta, timezone

        if not rss_crawler:
            push_pipeline_log(run_id, json.dumps({"event": "rss_skipped", "reason": "rss_crawler not wired", "run_id": run_id, "agent": "pipeline", "level": "warn", "timestamp": datetime.now(tz=timezone.utc).isoformat()}))
            return
        since = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()
        extraction_agent = getattr(request.app.state, "extraction_agent", None)
        if not extraction_agent:
            push_pipeline_log(run_id, json.dumps({"event": "rss_skipped", "reason": "extraction_agent not wired", "run_id": run_id, "agent": "pipeline", "level": "warn", "timestamp": datetime.now(tz=timezone.utc).isoformat()}))
            return
        feeds = [
            {"url": src.url, "company": comp.competitor}
            for comp in filtered
            for src in comp.sources
            if src.type == "rss" and src.url
        ]
        if not feeds:
            push_pipeline_log(run_id, json.dumps({"event": "rss_skipped", "reason": "no rss sources configured", "run_id": run_id, "agent": "pipeline", "level": "warn", "timestamp": datetime.now(tz=timezone.utc).isoformat()}))
            return

        push_pipeline_log(run_id, json.dumps({
            "event": "rss_fetch_started", "run_id": run_id, "agent": "pipeline", "level": "info",
            "feed_count": len(feeds), "companies": list({f["company"] for f in feeds}),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        from schemas.state import CrawlResult
        feed_results = await rss_crawler.fetch_multiple(feeds, since_timestamp=since)

        total_entries = sum(len(r.entries) for r in feed_results)
        push_pipeline_log(run_id, json.dumps({
            "event": "rss_fetch_completed", "run_id": run_id, "agent": "pipeline", "level": "info",
            "feeds_returned": len(feed_results), "total_entries": total_entries,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

        total_extracted = 0
        for feed_result in feed_results:
            company = next((f["company"] for f in feeds if f["url"] == feed_result.feed_url), None)
            if not company:
                continue
            push_pipeline_log(run_id, json.dumps({
                "event": "rss_feed_processing", "run_id": run_id, "agent": "pipeline", "level": "info",
                "company": company, "feed_url": feed_result.feed_url, "entry_count": len(feed_result.entries),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }))
            for entry in feed_result.entries:
                crawl_result = CrawlResult(
                    url=entry.url,
                    content=f"{entry.title}\n\n{entry.content}",
                    is_changed=True,
                    crawl_timestamp=entry.published_at,
                    content_hash=entry.entry_hash,
                    status_code=200,
                )
                push_pipeline_log(run_id, json.dumps({
                    "event": "extraction_started", "run_id": run_id, "agent": "pipeline", "level": "info",
                    "company": company, "title": entry.title[:80], "url": entry.url,
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                }))
                try:
                    await extraction_agent.run(crawl_result=crawl_result, company=company, run_id=run_id)
                    total_extracted += 1
                    push_pipeline_log(run_id, json.dumps({
                        "event": "extraction_completed", "run_id": run_id, "agent": "pipeline", "level": "info",
                        "company": company, "title": entry.title[:80],
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }))
                except Exception as exc:
                    push_pipeline_log(run_id, json.dumps({
                        "event": "extraction_failed", "run_id": run_id, "agent": "pipeline", "level": "error",
                        "company": company, "url": entry.url, "error": str(exc)[:200],
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }))
                    log.error("rss_extraction_failed_manual", source=entry.url, error=str(exc))

        push_pipeline_log(run_id, json.dumps({
            "event": "rss_pipeline_done", "run_id": run_id, "agent": "pipeline", "level": "info",
            "total_entries": total_entries, "total_extracted": total_extracted,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

    async def _run_tavily():
        import hashlib
        from datetime import datetime, timezone

        tavily = getattr(request.app.state, "tavily_search", None)
        extraction_agent = getattr(request.app.state, "extraction_agent", None)
        if not tavily or not extraction_agent:
            push_pipeline_log(run_id, json.dumps({"event": "tavily_skipped", "reason": "tavily or extraction_agent not wired", "run_id": run_id}))
            return

        from schemas.state import CrawlResult

        total_events = 0
        for comp in filtered:
            tavily_sources = [s for s in comp.sources if s.type == "tavily" and s.apify_query]
            for src in tavily_sources[:2]:  # max 2 per company to limit cost
                push_pipeline_log(run_id, json.dumps({
                    "event": "tavily_search_started", "run_id": run_id,
                    "agent": "pipeline", "level": "info",
                    "company": comp.competitor, "query": src.apify_query[:80],
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                }))
                try:
                    results = await tavily.search(query=src.apify_query, max_results=5, days=7)
                    push_pipeline_log(run_id, json.dumps({
                        "event": "tavily_search_completed", "run_id": run_id,
                        "agent": "pipeline", "level": "info",
                        "company": comp.competitor, "results": len(results),
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }))
                    for r in results:
                        now = datetime.now(tz=timezone.utc).isoformat()
                        content = f"{r.title}\n\n{r.content}"
                        content_hash = hashlib.sha256(content.encode()).hexdigest()
                        crawl_result = CrawlResult(
                            url=r.url,
                            content=content,
                            is_changed=True,
                            crawl_timestamp=r.published_date or now,
                            content_hash=content_hash,
                            status_code=200,
                        )
                        push_pipeline_log(run_id, json.dumps({
                            "event": "extraction_started", "run_id": run_id,
                            "agent": "pipeline", "level": "info",
                            "company": comp.competitor, "source": r.url[:80],
                            "timestamp": now,
                        }))
                        try:
                            result = await extraction_agent.run(
                                crawl_result=crawl_result, company=comp.competitor, run_id=run_id
                            )
                            total_events += len(result.events_extracted)
                            push_pipeline_log(run_id, json.dumps({
                                "event": "extraction_completed", "run_id": run_id,
                                "agent": "pipeline", "level": "info",
                                "company": comp.competitor, "source": r.url[:80],
                                "events_extracted": len(result.events_extracted),
                                "quarantined": result.quarantined_count,
                                "cost_usd": round(result.llm_cost_usd, 5) if hasattr(result, "llm_cost_usd") else 0,
                                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                            }))
                        except Exception as exc:
                            push_pipeline_log(run_id, json.dumps({
                                "event": "extraction_failed", "run_id": run_id,
                                "agent": "pipeline", "level": "error",
                                "company": comp.competitor, "source": r.url[:80],
                                "error": str(exc)[:200],
                                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                            }))
                except Exception as exc:
                    push_pipeline_log(run_id, json.dumps({
                        "event": "tavily_search_failed", "run_id": run_id,
                        "agent": "pipeline", "level": "error",
                        "company": comp.competitor, "error": str(exc)[:200],
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }))
                    log.error("tavily_search_failed_manual", company=comp.competitor, run_id=run_id, error=str(exc))

        push_pipeline_log(run_id, json.dumps({
            "event": "pipeline_done", "run_id": run_id, "status": "completed",
            "total_events": total_events,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }))

    # Fire and forget — return run_id immediately
    if body.mode == "full":
        asyncio.create_task(_run_full())
    elif body.mode == "rss":
        asyncio.create_task(_run_rss())
    elif body.mode == "tavily":
        asyncio.create_task(_run_tavily())
    else:
        raise HTTPException(status_code=422, detail=f"Unknown mode: {body.mode}. Use 'full', 'rss', or 'tavily'.")

    return PipelineTriggerResponse(
        run_id=run_id,
        mode=body.mode,
        companies=company_names,
        message=f"Pipeline triggered in background. Stream logs at /api/v1/pipeline/logs/{run_id}",
    )


# ── Pipeline live log stream ───────────────────────────────────────────────────

import asyncio as _asyncio
import collections

# In-memory ring buffer: run_id → deque of log line strings (max 500 per run)
_pipeline_log_buffer: dict[str, collections.deque] = {}
_pipeline_log_waiters: dict[str, list[_asyncio.Event]] = {}


def push_pipeline_log(run_id: str, line: str) -> None:
    """Called by pipeline/agents to push a log line to the live stream buffer."""
    if run_id not in _pipeline_log_buffer:
        _pipeline_log_buffer[run_id] = collections.deque(maxlen=500)
        _pipeline_log_waiters[run_id] = []
    _pipeline_log_buffer[run_id].append(line)
    for event in _pipeline_log_waiters.get(run_id, []):
        event.set()


@router.get("/pipeline/logs/{run_id}", tags=["ops"])
async def stream_pipeline_logs(run_id: str) -> StreamingResponse:
    """SSE stream of structured log lines for a pipeline run.

    Connect immediately after triggering a run. Each event is a JSON log line.
    Stream ends when the run completes (status != 'running') or after 10 minutes.

    Example:
        curl -N http://localhost:8000/api/v1/pipeline/logs/manual_abc123
    """
    import time

    async def _generate() -> AsyncGenerator[str, None]:
        sent_index = 0
        deadline = time.monotonic() + 600  # 10 min max

        # Send any already-buffered lines first
        buf = _pipeline_log_buffer.get(run_id, collections.deque())
        lines = list(buf)
        for line in lines:
            yield f"data: {line}\n\n"
        sent_index = len(lines)

        event_store: EventStore = getattr(
            # We access event_store through the closure — it's on app.state but
            # we don't have request here, so we use a module-level ref set in create_app.
            _app_state, "event_store", None
        )

        while time.monotonic() < deadline:
            # Check if run is still active
            if event_store:
                try:
                    state = await event_store.get_pipeline_state(run_id)
                    if state and state.get("status") not in (None, "running"):
                        # Flush remaining buffer then close
                        buf = _pipeline_log_buffer.get(run_id, collections.deque())
                        new_lines = list(buf)[sent_index:]
                        for line in new_lines:
                            yield f"data: {line}\n\n"
                        yield f"data: {json.dumps({'event': 'pipeline_done', 'status': state['status'], 'total_events': state.get('total_events', 0), 'total_cost_usd': round(state.get('total_cost_usd', 0.0), 4)})}\n\n"
                        return
                except Exception:
                    pass

            # Wait for new log lines (up to 2s)
            waiter = _asyncio.Event()
            _pipeline_log_waiters.setdefault(run_id, []).append(waiter)
            try:
                await _asyncio.wait_for(waiter.wait(), timeout=2.0)
            except _asyncio.TimeoutError:
                pass
            finally:
                waiters = _pipeline_log_waiters.get(run_id, [])
                if waiter in waiters:
                    waiters.remove(waiter)

            # Flush new lines; close on terminal event
            buf = _pipeline_log_buffer.get(run_id, collections.deque())
            new_lines = list(buf)[sent_index:]
            for line in new_lines:
                yield f"data: {line}\n\n"
                try:
                    parsed = json.loads(line)
                    if parsed.get("event") in ("pipeline_done", "rss_pipeline_done"):
                        return
                except Exception:
                    pass
            sent_index += len(new_lines)

        yield f"data: {json.dumps({'event': 'stream_timeout', 'message': 'Log stream timed out after 10 minutes'})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Module-level app state ref — set by create_app so the log stream can reach event_store
_app_state: Any = None


# ── Quarantine admin ───────────────────────────────────────────────────────────

class QuarantineListItem(BaseModel):
    quarantine_id: str
    source_url: str
    raw_content_excerpt: str
    extracted_event: dict[str, Any]
    confidence_score: float
    error_code: str
    error_details: str
    created_at: str
    status: str


class QuarantineStats(BaseModel):
    pending: int
    approved: int
    corrected: int
    rejected: int
    total: int
    correction_rate_by_event_type: dict[str, float]


class QuarantineAction(BaseModel):
    action: str = Field(..., description="'approve' | 'correct' | 'reject'")
    corrections: dict[str, Any] | None = Field(
        default=None,
        description="Field-level corrections when action is 'correct'. Keys are field names.",
    )


@router.get("/admin/quarantine", response_model=list[QuarantineListItem], tags=["admin"])
async def list_quarantine(
    limit: int = Query(default=50, ge=1, le=200),
    event_store: EventStore = Depends(get_event_store),
) -> list[QuarantineListItem]:
    """List pending quarantined events for human review."""
    try:
        docs = await event_store.get_pending_quarantine(limit=limit)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    return [
        QuarantineListItem(
            quarantine_id=str(doc.get("_id", doc.get("quarantine_id", ""))),
            source_url=doc.get("source_url", ""),
            raw_content_excerpt=doc.get("raw_content_excerpt", ""),
            extracted_event=doc.get("extracted_event", {}),
            confidence_score=doc.get("confidence_score", 0.0),
            error_code=doc.get("error_code", ""),
            error_details=doc.get("error_details", ""),
            created_at=doc.get("created_at", ""),
            status=doc.get("status", "pending"),
        )
        for doc in docs
    ]


@router.patch("/admin/quarantine/{quarantine_id}", tags=["admin"])
async def review_quarantine_event(
    quarantine_id: str,
    body: QuarantineAction,
    event_store: EventStore = Depends(get_event_store),
) -> dict[str, str]:
    """Review a quarantined event.

    Actions:
    - approve: Store the event as-is in the event store.
    - correct: Apply field corrections and store.
    - reject: Mark as rejected; event is not stored.

    Approved or corrected events are tagged human_reviewed=true.
    Corrections accumulate as training examples for DSPy (Phase 5).
    """
    valid_actions = {"approve", "correct", "reject"}
    if body.action not in valid_actions:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action '{body.action}'. Must be one of: {valid_actions}",
        )

    if body.action == "correct" and not body.corrections:
        raise HTTPException(
            status_code=422,
            detail="'correct' action requires a non-empty 'corrections' dict",
        )

    try:
        # Fetch the quarantine doc
        doc = await event_store.get_quarantine_by_id(quarantine_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"Quarantine item not found: {quarantine_id}")

        if body.action == "reject":
            await event_store.update_quarantine_status(quarantine_id, "rejected")
            log.info(
                "quarantine_rejected",
                action="review_quarantine",
                quarantine_id=quarantine_id,
            )
            return {"status": "rejected", "quarantine_id": quarantine_id}

        # approve or correct — store the event
        event_dict = dict(doc.get("extracted_event", {}))
        if body.action == "correct" and body.corrections:
            event_dict.update(body.corrections)
            event_dict["human_reviewed"] = True
            event_dict["human_corrected_fields"] = list(body.corrections.keys())

            # Save as training example for DSPy Phase 5
            training_example = {
                "source_text": doc.get("raw_content_excerpt", ""),
                "original_extraction": doc.get("extracted_event", {}),
                "corrected_extraction": event_dict,
                "corrected_fields": list(body.corrections.keys()),
                "event_type": event_dict.get("event_type", ""),
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            await event_store.insert_training_example(training_example)
        else:
            event_dict["human_reviewed"] = True

        event_id = await event_store.insert_event(event_dict)
        final_status = "corrected" if body.action == "correct" else "approved"
        await event_store.update_quarantine_status(
            quarantine_id,
            status=final_status,
            corrections=body.corrections,
        )

        log.info(
            "quarantine_approved",
            action="review_quarantine",
            quarantine_id=quarantine_id,
            event_id=event_id,
            action_taken=body.action,
        )
        return {"status": final_status, "quarantine_id": quarantine_id, "event_id": event_id}

    except HTTPException:
        raise
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc


@router.get("/admin/quarantine/stats", response_model=QuarantineStats, tags=["admin"])
async def quarantine_stats(
    event_store: EventStore = Depends(get_event_store),
) -> QuarantineStats:
    """Quarantine stats: counts by status and correction rate by event type."""
    try:
        stats = await event_store.get_quarantine_stats()
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    return QuarantineStats(
        pending=stats.get("pending", 0),
        approved=stats.get("approved", 0),
        corrected=stats.get("corrected", 0),
        rejected=stats.get("rejected", 0),
        total=stats.get("total", 0),
        correction_rate_by_event_type=stats.get("correction_rate_by_event_type", {}),
    )


# ── Events ─────────────────────────────────────────────────────────────────────

class EventListItem(BaseModel):
    event_id: str
    company: str
    event_type: str
    timestamp: str
    summary: str
    source_urls: list[str]
    confidence_score: float
    stakeholder_tags: list[str]


class EventListResponse(BaseModel):
    events: list[EventListItem]
    count: int
    company: str
    days: int


@router.get("/events", response_model=EventListResponse, tags=["intel"])
async def list_events(
    company: str = Query(..., description="Competitor company name"),
    days: int = Query(default=7, ge=1, le=365),
    event_type: str | None = Query(default=None, description="Filter by event type"),
    stakeholder: str | None = Query(default=None, description="Filter by stakeholder tag"),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
    event_store: EventStore = Depends(get_event_store),
) -> EventListResponse:
    """List recent events for a competitor, with optional filters."""
    try:
        if stakeholder:
            docs = await event_store.get_events_by_stakeholder(
                stakeholder_tag=stakeholder, days=days, limit=limit
            )
            docs = [d for d in docs if d.get("company") == company]
        else:
            event_types = [event_type] if event_type else None
            docs = await event_store.get_recent_events(
                company=company,
                days=days,
                event_types=event_types,
                min_confidence=min_confidence,
                limit=limit,
            )
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    events = [
        EventListItem(
            event_id=str(doc.get("_id", "")),
            company=doc.get("company", ""),
            event_type=doc.get("event_type", ""),
            timestamp=doc.get("timestamp", ""),
            summary=doc.get("summary", ""),
            source_urls=doc.get("source_urls", []),
            confidence_score=doc.get("confidence_score", 0.0),
            stakeholder_tags=doc.get("stakeholder_tags", []),
        )
        for doc in docs
    ]

    return EventListResponse(events=events, count=len(events), company=company, days=days)


# ── Threat scores (Phase 3 — served from pre-computed docs) ───────────────────

class ThreatScoreResponse(BaseModel):
    company: str
    score: float
    tier: str
    trend: str
    score_components: dict[str, float]
    narrative: str
    contributing_event_ids: list[str]
    generated_date: str


@router.get("/threats", response_model=list[ThreatScoreResponse], tags=["intel"])
async def list_threat_scores(
    event_store: EventStore = Depends(get_event_store),
) -> list[ThreatScoreResponse]:
    """Return latest threat scores for all tracked companies (pre-computed Sunday 7AM)."""
    try:
        docs = await event_store.get_latest_threat_scores()
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    return [
        ThreatScoreResponse(
            company=d["company"],
            score=d.get("score", 0.0),
            tier=d.get("tier", "LOW"),
            trend=d.get("trend", "stable"),
            score_components=d.get("score_components", {}),
            narrative=d.get("narrative", ""),
            contributing_event_ids=d.get("contributing_event_ids", []),
            generated_date=d.get("generated_date", ""),
        )
        for d in docs
    ]


@router.get("/threats/{company}", response_model=ThreatScoreResponse, tags=["intel"])
async def get_threat_score(
    company: str,
    event_store: EventStore = Depends(get_event_store),
) -> ThreatScoreResponse:
    """Return latest threat score for a specific company."""
    try:
        doc = await event_store.get_threat_score(company=company)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    if not doc:
        raise HTTPException(status_code=404, detail=f"No threat score found for: {company}")

    return ThreatScoreResponse(
        company=doc["company"],
        score=doc.get("score", 0.0),
        tier=doc.get("tier", "LOW"),
        trend=doc.get("trend", "stable"),
        score_components=doc.get("score_components", {}),
        narrative=doc.get("narrative", ""),
        contributing_event_ids=doc.get("contributing_event_ids", []),
        generated_date=doc.get("generated_date", ""),
    )


# ── Narratives (Phase 4 — weekly synthesis output) ────────────────────────────

class NarrativeResponse(BaseModel):
    narrative_id: str
    company: str
    narrative_title: str
    narrative_summary: str
    strategic_intent: str
    confidence: float
    constituent_event_ids: list[str]
    time_window_days: int
    key_signals: list[str]
    generated_date: str


@router.get("/narratives", response_model=list[NarrativeResponse], tags=["intel"])
async def list_narratives(
    company: str,
    days: int = 90,
    event_store: EventStore = Depends(get_event_store),
) -> list[NarrativeResponse]:
    """Return narrative events (weekly synthesis) for a competitor."""
    try:
        docs = await event_store.get_recent_events(
            company=company,
            days=days,
            event_types=["narrative"],
            limit=20,
        )
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    return [
        NarrativeResponse(
            narrative_id=str(doc.get("_id", "")),
            company=doc.get("company", ""),
            narrative_title=doc.get("narrative_title", ""),
            narrative_summary=doc.get("narrative_summary", doc.get("summary", "")),
            strategic_intent=doc.get("strategic_intent", ""),
            confidence=doc.get("confidence", doc.get("confidence_score", 0.0)),
            constituent_event_ids=doc.get("constituent_event_ids", []),
            time_window_days=doc.get("time_window_days", 90),
            key_signals=doc.get("key_signals", []),
            generated_date=doc.get("generated_date", doc.get("timestamp", "")),
        )
        for doc in docs
    ]


# ── Feature matrix (Phase 3 — served from pre-computed docs) ──────────────────

class FeatureMatrixResponse(BaseModel):
    company: str
    taxonomy_version: str
    last_updated: str
    features: dict[str, list[dict[str, Any]]]


@router.get("/matrix/{company}", response_model=FeatureMatrixResponse, tags=["intel"])
async def get_feature_matrix(
    company: str,
    event_store: EventStore = Depends(get_event_store),
) -> FeatureMatrixResponse:
    """Return the living feature comparison matrix for a competitor (pre-computed)."""
    try:
        doc = await event_store.get_feature_matrix(company=company)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from exc

    if not doc:
        raise HTTPException(status_code=404, detail=f"No feature matrix found for: {company}")

    # Normalize stored entries: old pipeline used "feature_name", current uses "name"
    raw_features: dict[str, list[dict[str, Any]]] = doc.get("features", {})
    normalized: dict[str, list[dict[str, Any]]] = {
        cat: [
            {**entry, "name": entry.get("name") or entry.get("feature_name", "")}
            for entry in entries
        ]
        for cat, entries in raw_features.items()
    }

    return FeatureMatrixResponse(
        company=doc["company"],
        taxonomy_version=doc.get("taxonomy_version", "1.0"),
        last_updated=doc.get("last_updated", ""),
        features=normalized,
    )


# ── Chat / SSE (Phase 3 — Conversational Agent) ───────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, description="Session ID for conversation history")
    stakeholder_role: str | None = Field(
        default=None,
        description="Stakeholder context: 'ceo', 'sales', 'marketing', 'product', 'customer_success'",
    )


class ChatMessage(BaseModel):
    role: str
    content: str
    sources: list[dict[str, str]] = Field(default_factory=list)
    confidence: float | None = None
    caveats: list[str] = Field(default_factory=list)


@router.post("/chat", tags=["chat"])
async def chat(
    body: ChatRequest,
    request: Request,
) -> StreamingResponse:
    """Conversational interface with SSE streaming.

    Returns a server-sent event stream. Each SSE event is a JSON chunk.
    Final event has type 'done' with full message + sources + confidence.

    The Conversational Agent (Phase 3) is wired here. Until Phase 3 is complete,
    this returns a stub response so the route is functional.
    """
    session_id = body.session_id or str(uuid.uuid4())

    conversational_agent = getattr(request.app.state, "conversational_agent", None)

    if conversational_agent is None:
        # Phase 3 not yet deployed — return stub
        async def _stub_stream() -> AsyncGenerator[str, None]:
            stub_text = (
                "Conversational Agent is not yet deployed (Phase 3). "
                "The research pipeline is running; events are being collected."
            )
            yield f"data: {json.dumps({'type': 'chunk', 'content': stub_text})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': stub_text, 'sources': [], 'confidence': None, 'session_id': session_id})}\n\n"

        return StreamingResponse(
            _stub_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Phase 3: delegate to Conversational Agent
    async def _agent_stream() -> AsyncGenerator[str, None]:
        try:
            async for chunk in conversational_agent.stream(
                message=body.message,
                session_id=session_id,
                stakeholder_role=body.stakeholder_role,
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as exc:
            log.error(
                "chat_stream_error",
                agent="api",
                action="chat",
                session_id=session_id,
                error=str(exc),
            )
            error_event = {
                "type": "error",
                "error_code": ErrorCode.AGENT_STATE_INVALID,
                "message": "An error occurred generating your response.",
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        _agent_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Dynamic competitor registration ───────────────────────────────────────────

class CompetitorRegistrationRequest(BaseModel):
    competitors: list[str] = Field(..., min_length=1, description="Competitor company names to add")
    domains: dict[str, str] = Field(
        default_factory=dict,
        description="Optional domain hints per competitor, e.g. {'BCG': 'bcg.com'}",
    )
    canonical_names: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Optional name aliases per competitor",
    )
    base_company: str = Field(default="", description="Your firm's name (used for context)")


class CompetitorRegistrationResponse(BaseModel):
    registered: list[str]
    skipped: list[str]
    sources_discovered: dict[str, int]
    message: str


@router.post("/competitors", response_model=CompetitorRegistrationResponse, tags=["config"])
async def register_competitors(
    body: CompetitorRegistrationRequest,
    request: Request,
) -> CompetitorRegistrationResponse:
    """Dynamically add competitors — sources are auto-discovered, no YAML editing needed.

    For each new competitor:
      1. Runs DiscoveryAgent to find RSS feeds, blog sections, and news sources
      2. Writes discovered sources to config/sources.yaml
      3. Updates the in-memory competitor list immediately

    Optionally pass a domain hint per competitor for faster, higher-quality discovery:
      {"competitors": ["BCG"], "domains": {"BCG": "bcg.com"}}
    """
    import yaml
    from pathlib import Path

    discovery_agent = getattr(request.app.state, "discovery_agent", None)

    sources_path = Path("config/sources.yaml")
    try:
        with open(sources_path) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {"competitors": []}

    existing = {c["competitor"].lower() for c in config.get("competitors", [])}
    registered: list[str] = []
    skipped: list[str] = []
    sources_discovered: dict[str, int] = {}

    for comp in body.competitors:
        if comp.lower() in existing:
            skipped.append(comp)
            continue

        domain = body.domains.get(comp)
        canonical = list({comp} | set(body.canonical_names.get(comp, [comp])))

        # Auto-discover sources via DiscoveryAgent
        yaml_sources: list[dict] = []
        if discovery_agent is not None:
            try:
                discovery = await discovery_agent.discover(comp, domain=domain)
                for src in discovery.sources:
                    yaml_sources.append({
                        "type": src.source_type,
                        "url": src.url,
                        "frequency": src.frequency,
                    })
                sources_discovered[comp] = len(yaml_sources)
                log.info(
                    "competitor_sources_discovered",
                    agent="api",
                    competitor=comp,
                    domain=discovery.domain,
                    source_count=len(yaml_sources),
                )
            except Exception as exc:
                log.warning("discovery_failed_fallback", competitor=comp, error=str(exc)[:120])
                # Fall back to Google News RSS as the minimum viable source
                yaml_sources = [{
                    "type": "rss",
                    "url": f"https://news.google.com/rss/search?q={comp.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en",
                    "frequency": "30min",
                }]
                sources_discovered[comp] = 1
        else:
            # DiscoveryAgent not wired — use Google News RSS as default
            yaml_sources = [{
                "type": "rss",
                "url": f"https://news.google.com/rss/search?q={comp.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en",
                "frequency": "30min",
            }]
            sources_discovered[comp] = 1

        entry = {
            "competitor": comp,
            "canonical_names": canonical,
            "sources": yaml_sources,
        }
        config.setdefault("competitors", []).append(entry)
        registered.append(comp)
        log.info("competitor_registered", agent="api", competitor=comp, base_company=body.base_company)

    if registered:
        with open(sources_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        # Update conversational agent's in-memory tracked set immediately
        conversational_agent = getattr(request.app.state, "conversational_agent", None)
        if conversational_agent is not None:
            for comp in registered:
                conversational_agent._tracked_companies.add(comp.lower())
                conversational_agent._tracked_display.append(comp)

    return CompetitorRegistrationResponse(
        registered=registered,
        skipped=skipped,
        sources_discovered=sources_discovered,
        message=(
            f"Registered {len(registered)} competitor(s) with auto-discovered sources. "
            + (f"Skipped {len(skipped)} already tracked." if skipped else "")
        ),
    )


@router.post("/competitors/{company_name}/rediscover", tags=["config"])
async def rediscover_competitor_sources(
    company_name: str,
    request: Request,
    domain: str | None = None,
) -> dict:
    """Force re-discovery of sources for an existing competitor.

    Clears the 7-day discovery cache and runs a fresh source discovery.
    Updates config/sources.yaml with the new sources found.

    Pass ?domain=bcg.com to provide a domain hint for faster discovery.
    """
    import yaml
    from pathlib import Path

    discovery_agent = getattr(request.app.state, "discovery_agent", None)
    if not discovery_agent:
        return {"error": "DiscoveryAgent not available", "company": company_name}

    # Invalidate cache so we get fresh results
    await discovery_agent.invalidate_cache(company_name)
    discovery = await discovery_agent.discover(company_name, domain=domain, force_refresh=True)

    # Update sources.yaml for this competitor
    sources_path = Path("config/sources.yaml")
    try:
        with open(sources_path) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {"competitors": []}

    for entry in config.get("competitors", []):
        if entry.get("competitor", "").lower() == company_name.lower():
            entry["sources"] = [
                {"type": src.source_type, "url": src.url, "frequency": src.frequency}
                for src in discovery.sources
            ]
            break

    with open(sources_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    log.info(
        "competitor_rediscovered",
        agent="api",
        competitor=company_name,
        domain=discovery.domain,
        source_count=len(discovery.sources),
    )
    return {
        "company": company_name,
        "domain": discovery.domain,
        "sources_found": len(discovery.sources),
        "sources": [{"type": s.source_type, "url": s.url, "via": s.discovered_via} for s in discovery.sources],
    }
