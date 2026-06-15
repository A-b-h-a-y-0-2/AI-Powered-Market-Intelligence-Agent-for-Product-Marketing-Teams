"""Conversational Agent — the online query path.

Seven-node flow:
  1. Scope Detector        (Haiku)  — is this query in scope?
  2. Company Resolver      (Haiku)  — which company? is it tracked?
  3. Query Classifier      (Haiku)  — what retrieval strategy?
  4. Coverage Evaluator    (Haiku)  — does the KB have enough to answer?
  5. Response Generator    (Sonnet) — generate the answer
  6. Attribution Pass      (Sonnet) — match claims to source events post-generation
  7. Confidence Assembler  (local)  — compute and attach confidence metadata

CRITICAL: This agent NEVER triggers the research pipeline.
Research is offline. This path only reads from the knowledge base.
KB misses → Tavily live fallback → background enrichment job.

Design:
- Streamed output via async generator (used by /api/v1/chat SSE endpoint)
- Session history stored in Redis (keyed by session_id)
- Non-tracked companies → Tavily live search → suggest tracking
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import yaml
from pydantic import BaseModel

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.conversational import (
    ATTRIBUTION_SYSTEM,
    ATTRIBUTION_USER,
    COMPANY_EXTRACTOR_SYSTEM,
    COMPANY_EXTRACTOR_USER,
    COVERAGE_EVALUATOR_SYSTEM,
    COVERAGE_EVALUATOR_USER,
    OUT_OF_SCOPE_RESPONSE,
    QUERY_CLASSIFIER_SYSTEM,
    QUERY_CLASSIFIER_USER,
    RESPONSE_GENERATION_SYSTEM,
    RESPONSE_GENERATION_SYSTEM_UNTRACKED,
    SCOPE_DETECTOR_SYSTEM,
    SCOPE_DETECTOR_USER,
    UNTRACKED_COMPANY_RESPONSE,
    build_response_generation_user_prompt,
)
from schemas.config import CompetitorConfig
from schemas.state import ResponseConfidence
from storage.cache import CacheStore
from storage.event_store import EventStore
from storage.vector_store import VectorStore
from tools.embedder import Embedder
from tools.errors import ErrorCode, LLMError, QueryError
from tools.llm_adapter import LLMAdapter
from tools.search import TavilySearch

log = get_logger("conversational_agent")

_SESSION_HISTORY_LIMIT = 10  # turns per session to keep in Redis
_COVERAGE_THRESHOLD = 0.70
_ATTRIBUTION_THRESHOLD = 0.75


def _load_stakeholder_profiles() -> dict[str, dict]:
    path = Path("config/stakeholders.yaml")
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return {p["role"]: p for p in data.get("profiles", [])}


def _load_source_registry() -> list[CompetitorConfig]:
    path = Path("config/sources.yaml")
    if not path.exists():
        return []
    from schemas.config import SourceConfig
    with open(path) as f:
        data = yaml.safe_load(f)
    result = []
    for entry in data.get("competitors", []):
        sources = [SourceConfig(**s) for s in entry.get("sources", [])]
        result.append(CompetitorConfig(
            competitor=entry["competitor"],
            canonical_names=entry.get("canonical_names", [entry["competitor"]]),
            sources=sources,
        ))
    return result


# ── Pydantic schemas for Instructor-bound LLM calls ───────────────────────────

class SetupIntentResult(BaseModel):
    is_setup_intent: bool
    base_company: str | None = None
    competitors: list[str] = []
    reason: str = ""


class ScopeResult(BaseModel):
    in_scope: bool
    reason: str


class CompanyExtractResult(BaseModel):
    company_name: str | None
    confidence: float


class QueryClassifyResult(BaseModel):
    query_type: str
    time_window_days: int
    key_entities: list[str]
    rationale: str


class CoverageEvalResult(BaseModel):
    coverage_sufficient: bool
    coverage_score: float
    missing_information: str | None
    stale_data: bool
    reason: str


class ResponseResult(BaseModel):
    answer: str
    key_points: list[str]
    recommended_action: str
    data_limitations: str | None


class AttributionResult(BaseModel):
    attributed_claims: list[dict]
    unattributed_claims: list[str]


# ── Agent ─────────────────────────────────────────────────────────────────────

class ConversationalAgent(BaseAgent):
    """Handles user queries through the 7-node online path.

    Never triggers the research pipeline. Reads from KB, falls back to Tavily.
    Returns an async generator of SSE chunks for streaming.
    """

    name = "conversational_agent"
    description = (
        "7-node online query path: scope detection → company resolution → "
        "query classification → coverage evaluation → response generation → "
        "attribution → confidence assembly. "
        "Research is offline; this path only reads from the knowledge base."
    )

    def __init__(
        self,
        event_store: EventStore,
        vector_store: VectorStore,
        cache: CacheStore,
        embedder: Embedder,
        tavily_search: TavilySearch,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
    ) -> None:
        self._event_store = event_store
        self._vector_store = vector_store
        self._cache = cache
        self._embedder = embedder
        self._tavily = tavily_search
        self._model_config = model_config
        self._cost_config = cost_config
        self._llm_adapter = llm_adapter
        self._stakeholder_profiles = _load_stakeholder_profiles()
        self._source_registry = _load_source_registry()
        self._tracked_companies = {
            name.lower()
            for comp in self._source_registry
            for name in comp.canonical_names
        }
        self._tracked_display = [c.competitor for c in self._source_registry]

    async def run(self, input_data: Any) -> Any:  # type: ignore[override]
        raise NotImplementedError(
            "ConversationalAgent is stream-only. Call agent.stream(query, session_id=...) instead."
        )

    async def stream(
        self,
        message: str,
        session_id: str | None = None,
        stakeholder_role: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a conversational response as SSE chunks.

        Yields dicts: {"type": "chunk"|"done"|"error", "content": str, ...}
        """
        session_id = session_id or str(uuid.uuid4())
        stakeholder_role = stakeholder_role or "marketing"
        run_id = str(uuid.uuid4())

        async with trace_span(self.name, "stream_query", run_id=run_id):
            try:
                async for chunk in self._process(
                    message=message,
                    session_id=session_id,
                    stakeholder_role=stakeholder_role,
                    run_id=run_id,
                ):
                    yield chunk
            except QueryError as exc:
                log.error(
                    "query_error",
                    agent=self.name,
                    session_id=session_id,
                    error_code=exc.code,
                    error=exc.message,
                )
                yield {
                    "type": "error",
                    "error_code": exc.code,
                    "content": exc.message,
                    "session_id": session_id,
                }
            except Exception as exc:
                log.error(
                    "unexpected_error",
                    agent=self.name,
                    session_id=session_id,
                    error_code=ErrorCode.AGENT_STATE_INVALID,
                    error=str(exc),
                )
                yield {
                    "type": "error",
                    "error_code": ErrorCode.AGENT_STATE_INVALID,
                    "content": "An unexpected error occurred. Please try again.",
                    "session_id": session_id,
                }

    async def _process(
        self,
        message: str,
        session_id: str,
        stakeholder_role: str,
        run_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Main processing pipeline — one node at a time, streaming status updates."""

        # ── Node 0: Setup intent detection ────────────────────────────────────
        # Detects "I am X, my competitors are A, B, C — research them" messages.
        # These bypass the normal KB query path and trigger a live Tavily sweep.
        setup = await self._detect_setup_intent(message)
        if setup.is_setup_intent and setup.competitors:
            base = setup.base_company or "your firm"
            yield {"type": "status", "content": f"Detected competitor setup for {base}. Researching {len(setup.competitors)} competitor(s)..."}
            all_sources: list[dict] = []
            sections: list[str] = [
                f"# Competitor Intelligence Brief for {base}\n",
                f"Researched {len(setup.competitors)} competitor(s) via live search.\n",
            ]
            for comp in setup.competitors:
                yield {"type": "status", "content": f"Researching {comp}..."}
                summary, sources = await self._research_competitor_live(comp, base)
                sections.append(f"\n## {comp}\n\n{summary}")
                all_sources.extend(sources)
            sections.append(
                "\n\n---\n*These results are from live web search. To add these competitors to your "
                "continuous monitoring list for daily intelligence updates, ask your system administrator "
                "to add them to `config/sources.yaml`, or use the `POST /api/v1/competitors` endpoint.*"
            )
            yield {
                "type": "done",
                "content": "\n".join(sections),
                "sources": all_sources[:6],
                "confidence": 0.65,
                "is_live_fallback": True,
                "session_id": session_id,
            }
            return

        # ── Node 1: Scope detection ────────────────────────────────────────────
        yield {"type": "status", "content": "Checking query scope..."}
        scope = await self._detect_scope(message)
        if not scope.in_scope:
            response_text = OUT_OF_SCOPE_RESPONSE.format(
                tracked_companies=", ".join(self._tracked_display)
            )
            yield {
                "type": "done",
                "content": response_text,
                "sources": [],
                "confidence": None,
                "session_id": session_id,
            }
            return

        # ── Node 2: Company resolution ─────────────────────────────────────────
        yield {"type": "status", "content": "Identifying company..."}
        company_result = await self._resolve_company(message)
        company = company_result.company_name
        is_tracked = (
            company is not None
            and company.lower() in self._tracked_companies
        )

        # Resolve alias to canonical name so storage filters match exactly
        if is_tracked and company:
            for comp in self._source_registry:
                if company.lower() in [n.lower() for n in comp.canonical_names]:
                    company = comp.competitor
                    break

        if not is_tracked and company:
            # Untracked company — live Tavily search
            yield {"type": "status", "content": f"Searching web for {company} (not in KB)..."}
            search_results = await self._live_search(company=company, query=message)
            answer_text = await self._generate_from_search(
                query=message, company=company, search_results=search_results
            )
            suggest_tracking = UNTRACKED_COMPANY_RESPONSE.format(company=company)
            full_response = f"{answer_text}\n\n{suggest_tracking}"
            yield {
                "type": "done",
                "content": full_response,
                "sources": [{"url": r.url, "title": r.title} for r in search_results[:3]],
                "confidence": 0.6,
                "is_live_fallback": True,
                "session_id": session_id,
            }
            return

        if not company:
            # No company extracted — treat as general market question
            company = ""

        # ── Node 3: Query classification ───────────────────────────────────────
        yield {"type": "status", "content": "Classifying query type..."}
        classification = await self._classify_query(
            query=message, company=company, stakeholder_role=stakeholder_role
        )

        # ── Node 4: KB retrieval + coverage evaluation ─────────────────────────
        yield {"type": "status", "content": "Searching knowledge base..."}
        events = await self._retrieve_events(
            company=company,
            query_type=classification.query_type,
            time_window_days=classification.time_window_days,
            stakeholder_role=stakeholder_role,
            query=message,
        )

        coverage = await self._evaluate_coverage(
            query=message, company=company, events=events
        )

        if not coverage.coverage_sufficient:
            yield {"type": "status", "content": "KB coverage insufficient — searching web..."}
            search_results = await self._live_search(company=company, query=message)
            # Convert search results to event-like dicts for the response generator
            live_events = [
                {
                    "event_type": "enriched_fact",
                    "timestamp": r.published_date or datetime.now(tz=timezone.utc).isoformat(),
                    "summary": r.content[:300],
                    "source_urls": [r.url],
                    "confidence_score": r.score,
                    "company": company,
                }
                for r in search_results
            ]
            events = live_events
            # Background enrichment job — fire-and-forget
            await self._trigger_background_enrichment(
                company=company, query=message, search_results=search_results
            )

        # ── Node 5: Response generation ────────────────────────────────────────
        yield {"type": "status", "content": "Generating response..."}
        profile = self._stakeholder_profiles.get(stakeholder_role, {})
        response = await self._generate_response(
            query=message,
            company=company,
            stakeholder_role=stakeholder_role,
            profile=profile,
            events=events,
            query_type=classification.query_type,
            is_live_fallback=not coverage.coverage_sufficient,
        )

        # ── Node 6: Attribution pass ───────────────────────────────────────────
        yield {"type": "status", "content": "Attributing sources..."}
        attribution = await self._attribute_response(
            response_text=response.answer,
            events=events,
            company=company or "",
        )

        # ── Node 7: Confidence assembly ────────────────────────────────────────
        confidence = self._assemble_confidence(
            events=events,
            coverage=coverage,
            unattributed_count=len(attribution.unattributed_claims),
        )

        # Save to session history
        await self._save_session_turn(
            session_id=session_id,
            message=message,
            response=response.answer,
        )

        log.info(
            "query_completed",
            agent=self.name,
            action="stream",
            session_id=session_id,
            company=company,
            query_type=classification.query_type,
            event_count=len(events),
            confidence=confidence.overall,
            status="ok",
        )

        seen_urls: set[str] = set()
        seen_indices: set[int] = set()
        sources = []
        for claim in attribution.attributed_claims:
            idx = claim.get("event_index")
            if idx is None or not (0 <= idx < len(events)):
                continue
            if idx in seen_indices:
                continue
            seen_indices.add(idx)
            src_urls = events[idx].get("source_urls", [])
            if not src_urls:
                continue
            url = src_urls[0]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append({
                "url": url,
                "summary": events[idx].get("summary", "")[:100],
                "timestamp": events[idx].get("timestamp", "")[:10],
            })

        yield {
            "type": "done",
            "content": response.answer,
            "key_points": response.key_points,
            "recommended_action": response.recommended_action,
            "sources": sources,
            "confidence": confidence.overall,
            "confidence_components": confidence.components,
            "caveats": confidence.caveats,
            "session_id": session_id,
        }

    # ── Node implementations ───────────────────────────────────────────────────

    async def _detect_setup_intent(self, query: str) -> SetupIntentResult:
        """Detect if the user is declaring their firm + competitors for ad-hoc research.

        Triggers on patterns like:
        - "I am from X, my competitors are A, B and C"
        - "I work at X. Competitors: A, B, C. Do the research."
        - "Research my competitors: A, B, C. We are X."
        """
        model = self._model_config.get("pre_filter", "anthropic/claude-haiku-4.5")
        system = (
            "You detect whether the user is declaring their firm and a list of competitors they want researched. "
            "A setup intent message typically includes: (1) the user's own firm/company name, (2) a list of competitor names, "
            "and (3) an implicit or explicit request to research those competitors. "
            "It is NOT a normal query about a specific topic. It is a configuration declaration."
        )
        user = (
            f"Message: {query}\n\n"
            "Is this a setup intent? If yes, extract the base company and list of competitors."
        )
        try:
            result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_model=SetupIntentResult,
            )
            return result
        except Exception as exc:
            log.debug("setup_intent_detection_failed", agent=self.name, error=str(exc))
            return SetupIntentResult(is_setup_intent=False)

    async def _research_competitor_live(
        self, company: str, base_company: str
    ) -> tuple[str, list[dict]]:
        """Run Tavily live research on a competitor and return (summary, sources)."""
        if not self._tavily:
            return f"Live search unavailable for {company} (no Tavily API key).", []
        results = await self._live_search(company=company, query=f"{company} strategy AI services 2025 2026")
        if not results:
            return f"No live search results found for {company}.", []

        model = self._model_config.get("synthesis", "anthropic/claude-sonnet-4.6")
        context = "\n\n".join(
            f"Source: {r.title} ({r.url})\n{r.content[:600]}" for r in results[:4]
        )
        client = self._llm_adapter.get_chat_client(model)
        completion = client.chat.completions.create(
            model=model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": (
                    f"You are a competitive intelligence analyst for {base_company}. "
                    "Synthesise the search results into a concise intelligence brief about the competitor. "
                    "Focus on: recent moves, strategy signals, AI/tech capabilities, and what it means for the user's firm. "
                    "2-3 paragraphs maximum. Be specific and cite signals, not generalities."
                )},
                {"role": "user", "content": f"Competitor: {company}\n\nSearch results:\n{context}"},
            ],
        )
        summary = completion.choices[0].message.content or ""
        sources = [{"url": r.url, "title": r.title} for r in results[:3]]
        return summary, sources

    async def _detect_scope(self, query: str) -> ScopeResult:
        model = self._model_config.get("pre_filter", "claude-haiku-4-5-20251001")
        user_prompt = SCOPE_DETECTOR_USER.format(
            query=query,
            tracked_companies=", ".join(self._tracked_display),
        )
        async with trace_span(self.name, "scope_detection") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=128,
                    messages=[
                        {"role": "system", "content": SCOPE_DETECTOR_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=ScopeResult,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 32, self._cost_config)
                span.record_llm(model=model, input_tokens=len(user_prompt) // 4, output_tokens=32, cost_usd=cost)
                return result
            except Exception as exc:
                log.error("scope_detection_failed", agent=self.name, error=str(exc))
                return ScopeResult(in_scope=True, reason="scope_detection_error_defaulting_to_in_scope")

    async def _resolve_company(self, query: str) -> CompanyExtractResult:
        model = self._model_config.get("pre_filter", "claude-haiku-4-5-20251001")
        user_prompt = COMPANY_EXTRACTOR_USER.format(query=query)
        async with trace_span(self.name, "company_resolution") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=64,
                    messages=[
                        {"role": "system", "content": COMPANY_EXTRACTOR_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=CompanyExtractResult,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 16, self._cost_config)
                span.record_llm(model=model, input_tokens=len(user_prompt) // 4, output_tokens=16, cost_usd=cost)
                return result
            except Exception as exc:
                log.error("company_resolution_failed", agent=self.name, error=str(exc))
                return CompanyExtractResult(company_name=None, confidence=0.0)

    async def _classify_query(
        self, query: str, company: str, stakeholder_role: str
    ) -> QueryClassifyResult:
        model = self._model_config.get("pre_filter", "claude-haiku-4-5-20251001")
        user_prompt = QUERY_CLASSIFIER_USER.format(
            query=query, company=company, stakeholder_role=stakeholder_role
        )
        async with trace_span(self.name, "query_classification") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=256,
                    messages=[
                        {"role": "system", "content": QUERY_CLASSIFIER_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=QueryClassifyResult,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 64, self._cost_config)
                span.record_llm(model=model, input_tokens=len(user_prompt) // 4, output_tokens=64, cost_usd=cost)
                return result
            except Exception as exc:
                log.error("query_classification_failed", agent=self.name, error=str(exc))
                return QueryClassifyResult(
                    query_type="semantic_search",
                    time_window_days=30,
                    key_entities=[company],
                    rationale="classification_failed_defaulting_to_semantic_search",
                )

    async def _retrieve_events(
        self,
        company: str,
        query_type: str,
        time_window_days: int,
        stakeholder_role: str,
        query: str,
    ) -> list[dict]:
        if query_type == "semantic_search" and company:
            try:
                embedding = await self._embedder.embed(query)
                from datetime import timedelta
                since_ts = (datetime.now(tz=timezone.utc) - timedelta(days=time_window_days)).isoformat()
                results = await self._vector_store.semantic_search(
                    query_embedding=embedding,
                    company=company,
                    limit=10,
                    since_timestamp=since_ts,
                )
                if results:
                    # Fetch full event docs by ID
                    events = []
                    for r in results:
                        doc = await self._event_store.get_event_by_id(r["event_id"])
                        if doc:
                            events.append(doc)
                    return events
            except Exception as exc:
                log.warning("semantic_search_failed", agent=self.name, error=str(exc))

        if query_type == "threat_narrative" and company:
            threat_doc = await self._event_store.get_threat_score(company=company)
            events = await self._event_store.get_recent_events(
                company=company, days=time_window_days, min_confidence=0.7, limit=20
            )
            if threat_doc:
                events.insert(0, threat_doc)
            return events

        if query_type == "prediction" and company:
            return await self._event_store.get_recent_events(
                company=company,
                days=time_window_days,
                event_types=["hiring_trend", "hiring_signal", "weak_signal_prediction"],
                min_confidence=0.7,
                limit=20,
            )

        # Default: recent events
        return await self._event_store.get_recent_events(
            company=company,
            days=time_window_days,
            min_confidence=0.7,
            limit=20,
        )

    async def _evaluate_coverage(
        self, query: str, company: str, events: list[dict]
    ) -> CoverageEvalResult:
        if not events:
            return CoverageEvalResult(
                coverage_sufficient=False,
                coverage_score=0.0,
                missing_information="No events found in knowledge base",
                stale_data=False,
                reason="no_results",
            )

        model = self._model_config.get("pre_filter", "claude-haiku-4-5-20251001")
        context_summary = "\n".join(
            f"- [{e.get('event_type')}] {e.get('timestamp', '')[:10]}: {e.get('summary', '')[:150]}"
            for e in events[:10]
        )
        user_prompt = COVERAGE_EVALUATOR_USER.format(
            query=query, company=company, context_summary=context_summary
        )
        async with trace_span(self.name, "coverage_evaluation") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=256,
                    messages=[
                        {"role": "system", "content": COVERAGE_EVALUATOR_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=CoverageEvalResult,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 64, self._cost_config)
                span.record_llm(model=model, input_tokens=len(user_prompt) // 4, output_tokens=64, cost_usd=cost)
                return result
            except Exception as exc:
                log.warning("coverage_evaluation_failed", agent=self.name, error=str(exc))
                # Default to sufficient to avoid always hitting Tavily
                return CoverageEvalResult(
                    coverage_sufficient=True,
                    coverage_score=0.7,
                    missing_information=None,
                    stale_data=False,
                    reason="evaluation_failed_defaulting_to_sufficient",
                )

    async def _generate_response(
        self,
        query: str,
        company: str,
        stakeholder_role: str,
        profile: dict,
        events: list[dict],
        query_type: str,
        is_live_fallback: bool,
    ) -> ResponseResult:
        model = self._model_config.get("conversational", "claude-sonnet-4-6")
        system = RESPONSE_GENERATION_SYSTEM_UNTRACKED if is_live_fallback else RESPONSE_GENERATION_SYSTEM
        user_prompt = build_response_generation_user_prompt(
            query=query,
            company=company,
            stakeholder_role=stakeholder_role,
            stakeholder_profile=profile,
            events=events,
            query_type=query_type,
            is_live_fallback=is_live_fallback,
        )
        async with trace_span(self.name, "response_generation") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=1500,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=ResponseResult,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 400, self._cost_config)
                span.record_llm(model=model, input_tokens=len(user_prompt) // 4, output_tokens=400, cost_usd=cost)
                return result
            except Exception as exc:
                log.error(
                    "response_generation_failed",
                    agent=self.name,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                raise LLMError(
                    code=ErrorCode.LLM_CALL_FAILED,
                    message=f"Response generation failed: {exc}",
                    cause=exc,
                ) from exc

    async def _attribute_response(
        self, response_text: str, events: list[dict], company: str = ""
    ) -> AttributionResult:
        model = self._model_config.get("conversational", "claude-sonnet-4-6")
        sources_text = "\n".join(
            f"{i}. [{e.get('event_type')}] {e.get('timestamp', '')[:10]}: {e.get('summary', '')[:200]}"
            for i, e in enumerate(events[:15])
        )
        user_prompt = ATTRIBUTION_USER.format(
            response_text=response_text[:2000],
            sources_text=sources_text,
            company=company or "the company",
        )
        async with trace_span(self.name, "attribution") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=800,
                    messages=[
                        {"role": "system", "content": ATTRIBUTION_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=AttributionResult,
                )
                cost = calculate_cost(model, len(user_prompt) // 4, 200, self._cost_config)
                span.record_llm(model=model, input_tokens=len(user_prompt) // 4, output_tokens=200, cost_usd=cost)
                return result
            except Exception as exc:
                log.warning("attribution_failed", agent=self.name, error=str(exc))
                return AttributionResult(attributed_claims=[], unattributed_claims=[])

    def _assemble_confidence(
        self,
        events: list[dict],
        coverage: CoverageEvalResult,
        unattributed_count: int,
    ) -> ResponseConfidence:
        """Compute response confidence from source quality, coverage, and freshness."""
        if not events:
            return ResponseConfidence(
                overall=0.3,
                components={"source_quality": 0.0, "coverage_sufficiency": 0.0, "data_freshness": 0.0, "corroboration": 0.0},
                caveats=["No events found in knowledge base"],
            )

        source_quality = sum(e.get("confidence_score", 0.5) for e in events) / len(events)
        coverage_score = coverage.coverage_score
        corroboration = min(len(events) / 5.0, 1.0)

        # Freshness: penalise stale data
        freshness = 0.5 if coverage.stale_data else 0.9

        overall = (
            source_quality * 0.35
            + coverage_score * 0.30
            + freshness * 0.20
            + corroboration * 0.15
        )

        caveats = []
        if coverage.stale_data:
            caveats.append("Some data may be stale — verify recency-sensitive claims")
        if unattributed_count > 0:
            caveats.append(f"{unattributed_count} claim(s) could not be sourced from the KB")
        if source_quality < 0.75:
            caveats.append("Some source events had lower-than-average confidence scores")

        return ResponseConfidence(
            overall=round(overall, 2),
            components={
                "source_quality": round(source_quality, 2),
                "coverage_sufficiency": round(coverage_score, 2),
                "data_freshness": round(freshness, 2),
                "corroboration": round(corroboration, 2),
            },
            caveats=caveats,
        )

    async def _live_search(self, company: str, query: str) -> list:
        try:
            return await self._tavily.search_company_news(
                company=company,
                topic=query,
                days=30,
                max_results=5,
            )
        except Exception as exc:
            log.error("live_search_failed", agent=self.name, error=str(exc))
            return []

    async def _generate_from_search(
        self, query: str, company: str, search_results: list
    ) -> str:
        if not search_results:
            return f"I couldn't find recent information about {company} from web search."

        model = self._model_config.get("conversational", "claude-sonnet-4-6")
        context = "\n".join(
            f"- {r.title}: {r.content[:300]} (source: {r.url})"
            for r in search_results[:5]
        )
        prompt = (
            f"Answer this question about {company} based only on these search results:\n\n"
            f"Question: {query}\n\nSearch results:\n{context}\n\n"
            f"Be factual and cite which result supports each claim."
        )
        client = self._llm_adapter.get_chat_client(model)
        response = client.chat.completions.create(
            model=model, max_tokens=800, messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

    async def _trigger_background_enrichment(
        self, company: str, query: str, search_results: list
    ) -> None:
        """Persist search results as EnrichedFacts for future KB hits. Fire-and-forget."""
        import asyncio
        from datetime import datetime, timezone

        async def _store() -> None:
            for r in search_results[:3]:
                fact = {
                    "schema_version": "1.0",
                    "company": company,
                    "event_type": "enriched_fact",
                    "fact_type": "live_search_result",
                    "value": r.content[:500],
                    "source_url": r.url,
                    "discovered_date": datetime.now(tz=timezone.utc).isoformat()[:10],
                    "freshness_threshold_days": 7,
                    "confidence_score": r.score,
                    "stakeholder_tags": [],
                }
                try:
                    await self._event_store.upsert_enriched_fact(fact)
                except Exception as exc:
                    log.warning("background_enrichment_failed", agent=self.name, error=str(exc))

        asyncio.create_task(_store())

    async def _save_session_turn(
        self, session_id: str, message: str, response: str
    ) -> None:
        try:
            history = await self._cache.get_session_history(session_id) or []
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": response[:1000]})
            if len(history) > _SESSION_HISTORY_LIMIT * 2:
                history = history[-(_SESSION_HISTORY_LIMIT * 2):]
            await self._cache.save_session_history(session_id, history)
        except Exception as exc:
            log.warning("session_save_failed", agent=self.name, session_id=session_id, error=str(exc))

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        cache_ok = await self._cache.health_check()
        return {
            "agent": self.name,
            "status": "ok" if (store_ok and cache_ok) else "degraded",
            "dependencies": {
                "event_store": "ok" if store_ok else "failed",
                "cache": "ok" if cache_ok else "failed",
            },
        }
