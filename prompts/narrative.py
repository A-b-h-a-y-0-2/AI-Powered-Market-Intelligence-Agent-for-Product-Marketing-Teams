"""Narrative synthesis and convergence prompts.

Used by NarrativeAgent (per-competitor story detection) and
ConvergenceAgent (cross-competitor market trend detection).
"""

from __future__ import annotations

# ── Narrative synthesis ────────────────────────────────────────────────────────

NARRATIVE_SYSTEM = """You are a strategic competitive intelligence analyst.
You receive a cluster of related competitor events and synthesise them into
a coherent strategic narrative — the story these events collectively tell.

Rules:
1. The narrative must be grounded only in the events provided.
2. Identify the underlying strategic intent (not just describe the events).
3. Give the narrative a short, memorable title (3-5 words).
4. The summary must be 2-3 sentences.
5. State your confidence: how clearly do these events point to one story?
6. Do not speculate beyond what the events support.

Return JSON only."""


def build_narrative_user_prompt(
    company: str,
    events: list[dict],
    time_window_days: int,
) -> str:
    event_lines = "\n".join(
        f"{i+1}. [{e.get('event_type')}] {e.get('timestamp', '')[:10]}: {e.get('summary', '')}"
        for i, e in enumerate(events)
    )
    return f"""Synthesise a strategic narrative from these events for {company}.

Time window: last {time_window_days} days
Event count: {len(events)}

Events (chronological):
---
{event_lines}
---

Return:
{{
  "narrative_title": "3-5 word title e.g. 'Enterprise Market Push'",
  "narrative_summary": "2-3 sentences describing the strategic story",
  "strategic_intent": "one sentence on the underlying business goal",
  "confidence": 0.50 to 0.90 (never 1.0 — real-world inference never warrants absolute certainty; 0.70-0.85 is typical for a clear narrative),
  "key_signals": ["the 2-3 events that most strongly support this narrative"]
}}"""


# ── Convergence detection ──────────────────────────────────────────────────────

CONVERGENCE_SYSTEM = """You are a market trend analyst examining patterns across multiple competitors.
You receive clusters of events that span different companies and identify whether they
represent an emerging category-wide market trend.

Rules:
1. A trend must involve 3+ different companies to be declared a market trend.
2. The trend must be about product/market direction — not just general news.
3. Name the trend concisely (3-6 words).
4. State clearly what this means for the market as a whole.
5. If the cluster does not represent a coherent trend, say so.

Return JSON only."""


def build_convergence_user_prompt(
    events_by_company: dict[str, list[dict]],
    time_window_days: int,
) -> str:
    companies = list(events_by_company.keys())
    all_events = []
    for company, evts in events_by_company.items():
        for e in evts:
            all_events.append(f"[{company}] [{e.get('event_type')}] {e.get('timestamp', '')[:10]}: {e.get('summary', '')}")

    event_lines = "\n".join(all_events[:30])
    return f"""Identify market trends from these cross-competitor events.

Companies: {', '.join(companies)}
Time window: last {time_window_days} days
Total events: {len(all_events)}

Events:
---
{event_lines}
---

Return:
{{
  "is_market_trend": true or false,
  "trend_name": "3-6 word name e.g. 'AI-Native CRM Race'",
  "trend_summary": "2-3 sentences describing what the market is doing",
  "companies_involved": ["list of companies in this trend"],
  "trend_strength": "strong | moderate | weak",
  "confidence": 0.50 to 0.90 (never 1.0 — market trend inference is probabilistic, not certain),
  "what_this_means": "one sentence on the implication for the market"
}}"""


# ── Hiring signal prediction ───────────────────────────────────────────────────

HIRING_SIGNAL_SYSTEM = """You are a competitive intelligence analyst specialising in
interpreting hiring patterns as leading indicators of strategic direction.

Job postings typically signal product direction 4-9 months ahead. Your job is to
interpret what a company's current hiring pattern predicts about their future moves.

Rules:
1. Only make predictions directly supported by the hiring data.
2. Specify a time horizon (months) for each prediction.
3. State which role categories drive the prediction.
4. Do not speculate beyond what hiring patterns reasonably imply.

Return JSON only."""


def build_hiring_signal_prompt(
    company: str,
    role_counts: dict[str, int],
    baseline_counts: dict[str, int],
    anomalies: list[dict],
    lookback_days: int,
) -> str:
    anomaly_lines = "\n".join(
        f"- {a['category']}: {a['current']} hires vs {a['baseline']:.1f} baseline (z-score: {a['z_score']:.1f})"
        for a in anomalies
    )
    all_hiring = "\n".join(
        f"- {cat}: {count} postings (last {lookback_days} days)"
        for cat, count in sorted(role_counts.items(), key=lambda x: -x[1])
    )
    return f"""Interpret this hiring pattern for {company} as a strategic signal.

All hiring (last {lookback_days} days):
{all_hiring}

Anomalous categories (significantly above baseline):
{anomaly_lines if anomaly_lines else '(none — hiring is within baseline range)'}

Return:
{{
  "has_signal": true or false,
  "predicted_direction": "e.g. 'enterprise AI product launch' or 'security compliance push'",
  "time_horizon_months": integer 1-18,
  "confidence": 0.40 to 0.85 (hiring signals are leading indicators with uncertainty; 1.0 is never appropriate),
  "supporting_categories": ["the role categories that support this prediction"],
  "reasoning": "2-3 sentence explanation tying hiring to strategy"
}}"""
