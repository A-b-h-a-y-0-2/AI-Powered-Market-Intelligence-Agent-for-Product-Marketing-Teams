"""Digest Agent — pre-generates Monday morning stakeholder digests.

Schedule: Sunday 8AM (after all synthesis agents complete).
One digest per stakeholder role per week, stored in MongoDB.

This pre-computation means Monday morning reads are instant:
the digest is already written before anyone opens the app.

Model: claude-sonnet-4-6 (synthesis + persona framing).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
import yaml
from pydantic import BaseModel

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from storage.event_store import EventStore
from tools.errors import ErrorCode
from tools.llm_adapter import LLMAdapter

log = get_logger("digest_agent")

_STAKEHOLDERS_PATH = Path("config/stakeholders.yaml")
_DIGEST_COLLECTION = "weekly_digests"


def _load_stakeholder_profiles() -> dict[str, dict]:
    if not _STAKEHOLDERS_PATH.exists():
        return {}
    with open(_STAKEHOLDERS_PATH) as f:
        data = yaml.safe_load(f)
    return {p["role"]: p for p in data.get("profiles", [])}


DIGEST_SYSTEM = """You are a senior competitive intelligence analyst generating a weekly digest
for a specific stakeholder at a B2B SaaS company.

The digest should feel like a Monday morning briefing — concise, urgent, and actionable.
Include only what changed THIS WEEK and what it means for the stakeholder's decisions.

Structure:
1. Lead: the single most important competitive development this week
2. Competitor developments: 3-5 bullet points, ordered by urgency
3. Market signals: patterns emerging across competitors
4. Recommended actions: 2-3 concrete things this stakeholder should do

Frame everything for the stakeholder's vocabulary and decision context.
Return JSON only."""


def build_digest_user_prompt(
    stakeholder_role: str,
    profile: dict,
    week_events: list[dict],
    threat_scores: list[dict],
    narratives: list[dict],
    predictions: list[dict],
    week_label: str,
) -> str:
    cares_about = ", ".join(profile.get("cares_about", []))
    vocabulary = profile.get("vocabulary_style", "professional")

    events_text = "\n".join(
        f"- [{e.get('company')}] [{e.get('event_type')}] {e.get('timestamp', '')[:10]}: {e.get('summary', '')}"
        for e in week_events[:30]
    )
    threats_text = "\n".join(
        f"- {t.get('company')}: {t.get('tier')} ({t.get('score', 0):.0f}/100, {t.get('trend')})"
        for t in threat_scores
    )
    narratives_text = "\n".join(
        f"- {n.get('company')}: {n.get('narrative_title', '')} — {n.get('narrative_summary', '')[:150]}"
        for n in narratives[:5]
    )

    return f"""Generate a weekly competitive intelligence digest for week of {week_label}.

Stakeholder: {profile.get('display_name', stakeholder_role)}
Cares about: {cares_about}
Communication style: {vocabulary}

Events this week:
---
{events_text if events_text else '(no events)'}
---

Current threat scores:
{threats_text if threats_text else '(none)'}

Strategic narratives:
{narratives_text if narratives_text else '(none)'}

Return JSON:
{{
  "week_label": "{week_label}",
  "stakeholder_role": "{stakeholder_role}",
  "lead": "single most important development",
  "competitor_developments": [
    {{"company": "name", "development": "what happened", "urgency": "high|medium|low"}}
  ],
  "market_signals": ["pattern 1", "pattern 2"],
  "recommended_actions": ["action 1", "action 2", "action 3"],
  "generated_at": "{datetime.now(tz=timezone.utc).isoformat()}"
}}"""


class DigestAgent(BaseAgent):
    """Pre-generates weekly stakeholder digests on Sunday 8AM."""

    name = "digest_agent"
    description = (
        "Pre-generates Monday morning competitive digests per stakeholder role. "
        "Pulls from threat scores, narratives, and recent events. "
        "Schedule: Sunday 8AM. Model: claude-sonnet-4-6."
    )

    def __init__(
        self,
        event_store: EventStore,
        model_config: dict,
        cost_config: dict,
        llm_adapter: LLMAdapter,
    ) -> None:
        self._event_store = event_store
        self._model_config = model_config
        self._cost_config = cost_config
        self._llm_adapter = llm_adapter
        self._stakeholder_profiles = _load_stakeholder_profiles()

    async def run(
        self,
        companies: list[str],
        run_id: str | None = None,
    ) -> list[dict]:
        """Generate and store weekly digests for all stakeholder roles."""
        run_id = run_id or str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)
        week_label = now.strftime("Week of %B %d, %Y")
        digests: list[dict] = []

        # Gather inputs once for all stakeholders
        week_events: list[dict] = []
        for company in companies:
            events = await self._event_store.get_recent_events(
                company=company, days=7, min_confidence=0.7, limit=50
            )
            week_events.extend(events)

        threat_scores = await self._event_store.get_latest_threat_scores()
        narratives = await self._event_store.get_recent_events(
            company="", days=7, event_types=["narrative"], limit=10
        ) if companies else []

        for role, profile in self._stakeholder_profiles.items():
            try:
                digest = await self._generate_digest(
                    stakeholder_role=role,
                    profile=profile,
                    week_events=week_events,
                    threat_scores=threat_scores,
                    narratives=narratives,
                    week_label=week_label,
                )
                await self._store_digest(digest)
                digests.append(digest)
            except Exception as exc:
                log.error(
                    "digest_failed",
                    agent=self.name,
                    stakeholder=role,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )

        log.info(
            "digest_completed",
            agent=self.name,
            action="run",
            run_id=run_id,
            digests=len(digests),
            status="completed",
        )
        return digests

    async def _generate_digest(
        self,
        stakeholder_role: str,
        profile: dict,
        week_events: list[dict],
        threat_scores: list[dict],
        narratives: list[dict],
        week_label: str,
    ) -> dict:
        model = self._model_config.get("synthesis", "claude-sonnet-4-6")

        # Filter events by stakeholder tags
        tag = profile.get("role", stakeholder_role)
        filtered_events = [
            e for e in week_events
            if tag in (e.get("stakeholder_tags") or [])
        ] or week_events[:20]

        user_prompt = build_digest_user_prompt(
            stakeholder_role=stakeholder_role,
            profile=profile,
            week_events=filtered_events,
            threat_scores=threat_scores,
            narratives=narratives,
            predictions=[],
            week_label=week_label,
        )

        class DigestSchema(BaseModel):
            week_label: str
            stakeholder_role: str
            lead: str
            competitor_developments: list[dict]
            market_signals: list[str]
            recommended_actions: list[str]
            generated_at: str

        async with trace_span(self.name, "generate_digest") as span:
            result = self._llm_adapter.get_instructor_client(model).chat.completions.create(
                model=model,
                max_tokens=2000,
                messages=[
                    {"role": "system", "content": DIGEST_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                response_model=DigestSchema,
            )
            cost = calculate_cost(model, len(user_prompt) // 4, 500, self._cost_config)
            span.record_llm(
                model=model,
                input_tokens=len(user_prompt) // 4,
                output_tokens=500,
                cost_usd=cost,
            )
            return result.model_dump()

    async def _store_digest(self, digest: dict) -> None:
        doc = {**digest, "stored_at": datetime.now(tz=timezone.utc).isoformat()}
        db = self._event_store._require_db()  # use same connection
        await db[_DIGEST_COLLECTION].replace_one(
            {"stakeholder_role": digest["stakeholder_role"], "week_label": digest["week_label"]},
            doc,
            upsert=True,
        )
        log.info(
            "digest_stored",
            agent=self.name,
            stakeholder=digest["stakeholder_role"],
            week=digest["week_label"],
            status="ok",
        )

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        return {
            "agent": self.name,
            "status": "ok" if store_ok else "degraded",
            "stakeholder_profiles": len(self._stakeholder_profiles),
            "dependencies": {"event_store": "ok" if store_ok else "failed"},
        }
