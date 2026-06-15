"""Intelligence Agent — generates persona-conditioned stakeholder insights on demand.

Input: Company name + stakeholder role + optional query + recent events.
Output: Structured insight object with key points, recommended actions, confidence notes.

Differs from ConversationalAgent: IntelligenceAgent generates insight objects
(structured, JSON-returnable). ConversationalAgent wraps it in a conversational UX.

Model: claude-sonnet-4-6 — synthesis task.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from prompts.intelligence import INTELLIGENCE_SYSTEM, build_intelligence_user_prompt
from storage.event_store import EventStore
from storage.vector_store import VectorStore
from tools.errors import ErrorCode, LLMError
from tools.llm_adapter import LLMAdapter

log = get_logger("intelligence_agent")

_STAKEHOLDERS_PATH = Path("config/stakeholders.yaml")


def _load_stakeholder_profiles() -> dict[str, dict]:
    """Load stakeholder profiles from YAML config. Fails loudly if missing."""
    if not _STAKEHOLDERS_PATH.exists():
        raise FileNotFoundError(f"Stakeholder profiles not found: {_STAKEHOLDERS_PATH}")
    with open(_STAKEHOLDERS_PATH) as f:
        data = yaml.safe_load(f)
    return {p["role"]: p for p in data.get("profiles", [])}


class InsightOutput(BaseModel):
    summary: str = Field(..., description="2-3 sentence executive summary")
    key_insights: list[dict[str, str]] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    company: str
    stakeholder_role: str
    generated_at: str
    event_count: int


class IntelligenceAgent(BaseAgent):
    """Generates persona-conditioned insights from the event knowledge base.

    On-demand — not scheduled. Called by ConversationalAgent for synthesis,
    multi-hop, and persona-framed queries.
    """

    name = "intelligence_agent"
    description = (
        "Generates stakeholder-specific competitive insights from retrieved events. "
        "Uses claude-sonnet-4-6 for synthesis. On-demand (not scheduled). "
        "Input: company + stakeholder role + events. Output: structured insight JSON."
    )

    def __init__(
        self,
        event_store: EventStore,
        vector_store: VectorStore,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
    ) -> None:
        self._event_store = event_store
        self._vector_store = vector_store
        self._model_config = model_config
        self._cost_config = cost_config
        self._llm_adapter = llm_adapter
        self._stakeholder_profiles = _load_stakeholder_profiles()

    async def run(
        self,
        company: str,
        stakeholder_role: str,
        query: str | None = None,
        days: int = 30,
        run_id: str | None = None,
    ) -> InsightOutput:
        """Generate stakeholder-specific insights for a company.

        Args:
            company: Canonical company name from source registry.
            stakeholder_role: One of: ceo, sales, marketing, product, customer_success.
            query: Optional specific question to focus the insight.
            days: How many days of history to retrieve.
            run_id: Optional trace ID.

        Returns:
            InsightOutput with summary, key_insights, and recommended_actions.
        """
        run_id = run_id or str(uuid.uuid4())

        profile = self._stakeholder_profiles.get(stakeholder_role)
        if not profile:
            # Fall back to a generic profile
            profile = {
                "role": stakeholder_role,
                "display_name": stakeholder_role.replace("_", " ").title(),
                "cares_about": ["product features", "pricing", "competitive position"],
                "decision_context": "General competitive intelligence",
                "vocabulary_style": "professional",
                "default_stakeholder_tags": [stakeholder_role],
            }

        log.info(
            "intelligence_started",
            agent=self.name,
            action="run",
            competitor=company,
            stakeholder=stakeholder_role,
            has_query=query is not None,
            run_id=run_id,
        )

        # Retrieve relevant events — filter by stakeholder tag for efficiency
        tag = profile.get("role", stakeholder_role)
        events = await self._event_store.get_events_by_stakeholder(
            stakeholder_tag=tag, days=days, limit=30
        )
        # Keep only events for this company
        events = [e for e in events if e.get("company") == company]

        # If no results via tag, fall back to all events for company
        if not events:
            events = await self._event_store.get_recent_events(
                company=company, days=days, min_confidence=0.7, limit=30
            )

        log.info(
            "events_retrieved",
            agent=self.name,
            action="run",
            competitor=company,
            event_count=len(events),
        )

        # Generate insight
        insight = await self._generate_insight(
            company=company,
            stakeholder_role=stakeholder_role,
            profile=profile,
            events=events,
            query=query,
        )

        return InsightOutput(
            summary=insight.get("summary", ""),
            key_insights=insight.get("key_insights", []),
            recommended_actions=insight.get("recommended_actions", []),
            confidence_notes=insight.get("confidence_notes", []),
            company=company,
            stakeholder_role=stakeholder_role,
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            event_count=len(events),
        )

    async def _generate_insight(
        self,
        company: str,
        stakeholder_role: str,
        profile: dict,
        events: list[dict],
        query: str | None,
    ) -> dict[str, Any]:
        """Call claude-sonnet-4-6 to generate structured insight."""
        model = self._model_config.get("synthesis", "claude-sonnet-4-6")
        user_prompt = build_intelligence_user_prompt(
            company=company,
            stakeholder_role=stakeholder_role,
            stakeholder_profile=profile,
            events=events,
            query=query,
        )

        class InsightSchema(BaseModel):
            summary: str
            key_insights: list[dict]
            recommended_actions: list[str]
            confidence_notes: list[str]

        async with trace_span(self.name, "generate_insight") as span:
            try:
                result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                    model=model,
                    max_tokens=1500,
                    messages=[
                        {"role": "system", "content": INTELLIGENCE_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=InsightSchema,
                )
                input_tokens = len(user_prompt) // 4
                output_tokens = 400
                cost = calculate_cost(model, input_tokens, output_tokens, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
                return result.model_dump()
            except Exception as exc:
                log.error(
                    "insight_generation_failed",
                    agent=self.name,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    competitor=company,
                    error=str(exc),
                )
                raise LLMError(
                    code=ErrorCode.LLM_CALL_FAILED,
                    message=f"Insight generation failed for {company}: {exc}",
                    cause=exc,
                ) from exc

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if store_ok else "degraded",
            "dependencies": {"event_store": "ok" if store_ok else "failed"},
        }
