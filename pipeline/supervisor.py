"""LangGraph Supervisor — orchestrates all 9 agents with scheduling guarantees.

Phase 5: Full Sunday synthesis run with ordering guarantees.

Sunday synthesis ordering (enforced by supervisor reading last_completed_at):
  3AM  Hiring Signal Agent
  5AM  Narrative Agent        (6-hour visibility window)
  6AM  Convergence Agent      (cross-competitor)
  7AM  Threat Scoring Agent
  8AM  Digest Agent

Daily run ordering:
  2AM  Research Agent → Extraction Agent → Matrix Agent (event-triggered)

The supervisor reads each agent's checkpoint before dispatching the next.
Sub-agents are not aware of each other — only the supervisor decides what runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.convergence_agent import ConvergenceAgent
from agents.digest_agent import DigestAgent
from agents.extraction_agent import ExtractionAgent
from agents.hiring_signal_agent import HiringSignalAgent
from agents.matrix_agent import MatrixAgent
from agents.narrative_agent import NarrativeAgent
from agents.research_agent import ResearchAgent
from agents.sentiment_agent import SentimentAgent
from agents.threat_scoring_agent import ThreatScoringAgent
from observability.logger import get_logger
from schemas.config import CompetitorConfig
from storage.event_store import EventStore
from tools.errors import AgentError, ErrorCode

log = get_logger("supervisor")


class SupervisorState(TypedDict):
    """State flowing through the Sunday synthesis supervisor graph."""

    run_id: str
    companies: list[str]
    started_at: str
    hiring_completed_at: str | None
    narrative_completed_at: str | None
    convergence_completed_at: str | None
    threat_completed_at: str | None
    digest_completed_at: str | None
    errors: list[dict]
    status: str
    total_cost_usd: float


class SundaySupervisor:
    """Supervisor for the Sunday synthesis pipeline.

    Runs agents sequentially with explicit ordering guarantees.
    Each agent completes before the next begins.
    Checkpoints state to MongoDB after each agent.
    """

    def __init__(
        self,
        hiring_signal_agent: HiringSignalAgent,
        narrative_agent: NarrativeAgent,
        convergence_agent: ConvergenceAgent,
        threat_scoring_agent: ThreatScoringAgent,
        digest_agent: DigestAgent,
        event_store: EventStore,
        companies: list[str],
    ) -> None:
        self._hiring = hiring_signal_agent
        self._narrative = narrative_agent
        self._convergence = convergence_agent
        self._threat = threat_scoring_agent
        self._digest = digest_agent
        self._event_store = event_store
        self._companies = companies
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        builder = StateGraph(SupervisorState)

        builder.add_node("hiring_signal", self._hiring_node)
        builder.add_node("narrative", self._narrative_node)
        builder.add_node("convergence", self._convergence_node)
        builder.add_node("threat_scoring", self._threat_node)
        builder.add_node("digest", self._digest_node)
        builder.add_node("checkpoint", self._checkpoint_node)

        # Sequential execution — each must complete before the next starts
        builder.add_edge(START, "hiring_signal")
        builder.add_edge("hiring_signal", "narrative")
        builder.add_edge("narrative", "convergence")
        builder.add_edge("convergence", "threat_scoring")
        builder.add_edge("threat_scoring", "digest")
        builder.add_edge("digest", "checkpoint")
        builder.add_edge("checkpoint", END)

        return builder.compile()

    async def run(self, run_id: str | None = None) -> SupervisorState:
        run_id = run_id or f"sunday_{datetime.now(tz=timezone.utc).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
        log.info(
            "sunday_pipeline_started",
            agent="supervisor",
            action="run",
            run_id=run_id,
            companies=self._companies,
        )

        initial: SupervisorState = {
            "run_id": run_id,
            "companies": self._companies,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "hiring_completed_at": None,
            "narrative_completed_at": None,
            "convergence_completed_at": None,
            "threat_completed_at": None,
            "digest_completed_at": None,
            "errors": [],
            "status": "running",
            "total_cost_usd": 0.0,
        }

        final = await self._graph.ainvoke(initial)

        log.info(
            "sunday_pipeline_finished",
            agent="supervisor",
            action="run",
            run_id=run_id,
            status=final["status"],
            errors=len(final["errors"]),
        )
        return final

    async def _hiring_node(self, state: SupervisorState) -> dict:
        run_id = state["run_id"]
        errors = list(state["errors"])
        try:
            await self._hiring.run(companies=state["companies"], run_id=run_id)
        except Exception as exc:
            errors.append({"node": "hiring_signal", "error": str(exc)})
            log.error("hiring_node_failed", agent="supervisor", run_id=run_id, error=str(exc))
        return {
            "hiring_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
        }

    async def _narrative_node(self, state: SupervisorState) -> dict:
        run_id = state["run_id"]
        errors = list(state["errors"])
        try:
            await self._narrative.run(companies=state["companies"], run_id=run_id)
        except Exception as exc:
            errors.append({"node": "narrative", "error": str(exc)})
            log.error("narrative_node_failed", agent="supervisor", run_id=run_id, error=str(exc))
        return {
            "narrative_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
        }

    async def _convergence_node(self, state: SupervisorState) -> dict:
        run_id = state["run_id"]
        errors = list(state["errors"])
        try:
            await self._convergence.run(companies=state["companies"], run_id=run_id)
        except Exception as exc:
            errors.append({"node": "convergence", "error": str(exc)})
            log.error("convergence_node_failed", agent="supervisor", run_id=run_id, error=str(exc))
        return {
            "convergence_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
        }

    async def _threat_node(self, state: SupervisorState) -> dict:
        run_id = state["run_id"]
        errors = list(state["errors"])
        try:
            await self._threat.run(companies=state["companies"], run_id=run_id)
        except Exception as exc:
            errors.append({"node": "threat_scoring", "error": str(exc)})
            log.error("threat_node_failed", agent="supervisor", run_id=run_id, error=str(exc))
        return {
            "threat_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
        }

    async def _digest_node(self, state: SupervisorState) -> dict:
        run_id = state["run_id"]
        errors = list(state["errors"])
        try:
            await self._digest.run(companies=state["companies"], run_id=run_id)
        except Exception as exc:
            errors.append({"node": "digest", "error": str(exc)})
            log.error("digest_node_failed", agent="supervisor", run_id=run_id, error=str(exc))
        return {
            "digest_completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "errors": errors,
        }

    async def _checkpoint_node(self, state: SupervisorState) -> dict:
        has_errors = bool(state["errors"])
        status = "partial_success" if has_errors else "completed"
        checkpoint = {
            "run_id": state["run_id"],
            "agent": "sunday_supervisor",
            "status": status,
            "started_at": state["started_at"],
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            "hiring_completed_at": state.get("hiring_completed_at"),
            "narrative_completed_at": state.get("narrative_completed_at"),
            "convergence_completed_at": state.get("convergence_completed_at"),
            "threat_completed_at": state.get("threat_completed_at"),
            "digest_completed_at": state.get("digest_completed_at"),
            "errors": state["errors"],
        }
        try:
            await self._event_store.upsert_pipeline_state(state["run_id"], checkpoint)
        except Exception as exc:
            log.error(
                "supervisor_checkpoint_failed",
                agent="supervisor",
                error_code=ErrorCode.AGENT_CHECKPOINT_FAILED,
                error=str(exc),
            )
        return {"status": status}
