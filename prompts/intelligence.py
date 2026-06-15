"""Intelligence and synthesis prompts.

Used by IntelligenceAgent and ThreatScoringAgent.
System prompts are role definitions that never change per-request.
User prompts are built from typed inputs.
"""

from __future__ import annotations

# ── Stakeholder intelligence ──────────────────────────────────────────────────

INTELLIGENCE_SYSTEM = """You are a senior market intelligence analyst generating stakeholder-specific
competitive insights for a B2B SaaS company's go-to-market team.

Your job is to synthesise structured events from the competitive intelligence knowledge base
into clear, actionable insights framed for a specific stakeholder persona.

Rules:
1. Only state what is directly supported by the events in the context. Do not speculate.
2. Frame every insight for the stakeholder's decision context and vocabulary style.
3. Be specific: name the company, the feature, the pricing tier, the date.
4. Highlight what changed — not what has always been true.
5. If the events are ambiguous or sparse, say so explicitly. Do not fill gaps with assumptions.
6. End with 1–2 recommended actions the stakeholder can take based on this intelligence.

Return a JSON object with the fields: summary, key_insights (list), recommended_actions (list),
confidence_notes (list of caveats). No prose outside the JSON."""


def build_intelligence_user_prompt(
    company: str,
    stakeholder_role: str,
    stakeholder_profile: dict,
    events: list[dict],
    query: str | None = None,
) -> str:
    """Build the per-request prompt for stakeholder-specific insight generation."""
    events_text = _format_events(events)
    cares_about = ", ".join(stakeholder_profile.get("cares_about", []))
    vocabulary = stakeholder_profile.get("vocabulary_style", "professional")
    decision_context = stakeholder_profile.get("decision_context", "")
    query_section = f"\nSpecific question: {query}" if query else ""

    return f"""Generate market intelligence insights about {company} for a {stakeholder_role}.

Stakeholder context:
- Role: {stakeholder_profile.get('display_name', stakeholder_role)}
- Cares about: {cares_about}
- Decision context: {decision_context}
- Communication style: {vocabulary}
{query_section}

Recent events from knowledge base ({len(events)} events):
---
{events_text}
---

Generate insights framed for this stakeholder. Be specific and cite events by their summary.

Return JSON:
{{
  "summary": "2-3 sentence executive summary of the competitive situation",
  "key_insights": [
    {{
      "insight": "specific, actionable insight",
      "supporting_event": "which event supports this",
      "urgency": "high | medium | low"
    }}
  ],
  "recommended_actions": ["action 1 for this stakeholder", "action 2"],
  "confidence_notes": ["caveat 1 if any", "caveat 2 if any"]
}}"""


def _format_events(events: list[dict]) -> str:
    """Format events for inclusion in a prompt."""
    if not events:
        return "(no events available)"
    lines = []
    for i, event in enumerate(events[:20], 1):  # cap at 20 to manage context
        lines.append(
            f"{i}. [{event.get('event_type', 'unknown')}] {event.get('timestamp', '')[:10]} — "
            f"{event.get('summary', '')} (confidence: {event.get('confidence_score', 0):.2f})"
        )
    return "\n".join(lines)


# ── Threat scoring narrative ───────────────────────────────────────────────────

THREAT_NARRATIVE_SYSTEM = """You are a competitive threat analyst. Given a competitor's threat score
components and the events that drove them, write a single-sentence narrative explaining WHY
this competitor is rated at their current threat tier.

The narrative must:
- Name the specific signals that raised the score (e.g. "a pricing change + 3 AI features in 30 days")
- State the threat tier explicitly
- Be under 150 characters
- Contain no speculation — only what the events show

Return only the narrative string. No JSON, no prose."""


def build_threat_narrative_prompt(
    company: str,
    score: float,
    tier: str,
    velocity_score: float,
    type_score: float,
    recency_score: float,
    top_events: list[dict],
) -> str:
    events_summary = "; ".join(
        f"{e.get('event_type', '')} on {e.get('timestamp', '')[:10]}"
        for e in top_events[:5]
    )
    return f"""Write a one-sentence threat narrative for {company}.

Score: {score:.1f}/100 ({tier} tier)
Components: velocity={velocity_score:.1f}, type_weight={type_score:.1f}, recency={recency_score:.1f}
Top events: {events_summary}

Return only the narrative sentence."""
