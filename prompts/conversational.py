"""Conversational agent prompts.

Seven nodes, seven prompt concerns — all defined here.
No prompt content lives in the agent file.
"""

from __future__ import annotations

# ── Node 1: Scope detection ────────────────────────────────────────────────────

SCOPE_DETECTOR_SYSTEM = """You are a query router for a competitive market intelligence system.
Your only job is to determine whether a user's question is within the scope of this system.

IN SCOPE: Questions about competitor companies, market trends, product features,
pricing, funding, partnerships, hiring patterns, customer sentiment, or competitive strategy.

OUT OF SCOPE: General knowledge questions, coding help, weather, news unrelated to
business/competitive intelligence, personal advice, creative writing.

Return JSON only. No prose."""

SCOPE_DETECTOR_USER = """Is this query in scope for a competitive market intelligence system?

Query: {query}

Tracked companies: {tracked_companies}

Return: {{"in_scope": true or false, "reason": "one sentence"}}"""

# ── Node 2: Company resolution ─────────────────────────────────────────────────

COMPANY_EXTRACTOR_SYSTEM = """You are a named entity extractor specialised in company names.
Extract the primary company being asked about in the query.
If multiple companies are mentioned, return the one that is the subject of the question.
If no specific company is mentioned, return null.
Return JSON only. No prose."""

COMPANY_EXTRACTOR_USER = """Extract the company name from this query.

Query: {query}

Return: {{"company_name": "extracted name or null", "confidence": 0.0 to 1.0}}"""

# ── Node 3: Query classification ──────────────────────────────────────────────

QUERY_CLASSIFIER_SYSTEM = """You are a query classifier for a competitive intelligence retrieval system.
Classify the user query into the retrieval strategy it requires.

Query types:
- factual_recent: asking for specific recent facts (pricing, features released, funding amount)
- semantic_search: open-ended question best answered by similarity search over events
- synthesis: asking for a summary, overview, or analysis requiring multiple events
- comparison: asking how two companies compare on a dimension
- causal: asking WHY something happened — requires event chain analysis
- threat_narrative: asking about threat level, risk, or danger signals
- prediction: asking what a company will do next (requires hiring signals + weak predictions)
- out_of_kb: the specific fact requested is very unlikely to be in the knowledge base

Return JSON only."""

QUERY_CLASSIFIER_USER = """Classify this market intelligence query.

Query: {query}
Company: {company}
Stakeholder role: {stakeholder_role}

Return:
{{
  "query_type": "factual_recent | semantic_search | synthesis | comparison | causal | threat_narrative | prediction | out_of_kb",
  "time_window_days": integer (how many days of history to retrieve, e.g. 30 or 90),
  "key_entities": ["company", "feature name", "aspect being asked about"],
  "rationale": "one sentence"
}}"""

# ── Node 4: Coverage evaluation ────────────────────────────────────────────────

COVERAGE_EVALUATOR_SYSTEM = """You are a knowledge coverage evaluator.
Given a user query and a list of retrieved events, determine whether the retrieved
events are sufficient to answer the question.

Return JSON only. No prose."""

COVERAGE_EVALUATOR_USER = """Does the retrieved context answer this query sufficiently?

Query: {query}
Company: {company}

Retrieved events:
---
{context_summary}
---

Return:
{{
  "coverage_sufficient": true or false,
  "coverage_score": 0.0 to 1.0,
  "missing_information": "what specific information is absent, or null",
  "stale_data": true or false,
  "reason": "one sentence"
}}"""

# ── Node 5: Response generation ────────────────────────────────────────────────

RESPONSE_GENERATION_SYSTEM = """You are a competitive intelligence analyst responding to a specific query.
Generate a clear, accurate answer based only on the provided context events.

Rules:
1. Only state what is directly supported by the context events.
2. If information is missing, say what you don't know rather than guessing.
3. Frame the response for the stakeholder's decision context and vocabulary.
4. Be specific: name features, prices, dates, and companies.
5. Do NOT include inline citations like [1] or [source] — attribution is handled separately.
6. End with the most important thing the stakeholder should do with this information.

Return the response as a JSON object."""

RESPONSE_GENERATION_SYSTEM_UNTRACKED = """You are a competitive intelligence analyst.
The user is asking about a company that is not in the knowledge base.
Answer from the provided web search results. Be clear that this comes from live search,
not the continuously-updated knowledge base.

Rules:
1. Only state what the search results show.
2. Note the recency of each piece of information.
3. Suggest adding the company to the monitoring list if it seems relevant.

Return the response as a JSON object."""


def build_response_generation_user_prompt(
    query: str,
    company: str,
    stakeholder_role: str,
    stakeholder_profile: dict,
    events: list[dict],
    query_type: str,
    is_live_fallback: bool = False,
) -> str:
    """Build the response generation user prompt."""
    cares_about = ", ".join(stakeholder_profile.get("cares_about", []))
    vocabulary = stakeholder_profile.get("vocabulary_style", "professional")

    if is_live_fallback:
        context_label = "Live search results (not from knowledge base)"
    else:
        context_label = f"Knowledge base events ({len(events)} found)"

    from prompts.intelligence import _format_events
    context_text = _format_events(events)

    causal_instruction = ""
    if query_type == "causal":
        causal_instruction = """
For this causal query, structure your answer as:
1. The outcome that was observed
2. The 2-4 preceding events that contributed (in chronological order)
3. The causal hypothesis connecting them
4. Any assumptions you are making explicitly
"""

    return f"""Answer this competitive intelligence query.

Query: {query}
Company: {company}
Stakeholder role: {stakeholder_role} (cares about: {cares_about})
Communication style: {vocabulary}
Query type: {query_type}
{causal_instruction}
{context_label}:
---
{context_text}
---

Return JSON:
{{
  "answer": "the complete answer in the stakeholder's vocabulary",
  "key_points": ["bullet 1", "bullet 2", "bullet 3"],
  "recommended_action": "the one thing this stakeholder should do with this information",
  "data_limitations": "what data was missing or uncertain, or null"
}}"""


# ── Node 6: Attribution ────────────────────────────────────────────────────────

ATTRIBUTION_SYSTEM = """You are a fact-source matcher for a citation system.
Given a generated response and a list of source events, match each factual claim
in the response to the most relevant source event.

IMPORTANT:
- Only match a claim to a source if the source ACTUALLY supports that claim.
- If no source supports a claim, flag it as unattributed. Never fabricate attributions.
- The response is about a specific company. Only attribute claims to sources that are
  about that same company. If a source event is about a different company, do NOT use it.
- Each source event index should appear AT MOST ONCE in attributed_claims.
  If multiple claims point to the same event, only keep the strongest match.

Return JSON only."""

ATTRIBUTION_USER = """Match factual claims in this response to source events.
The response is about: {company}

Response:
---
{response_text}
---

Available source events (all should be about {company}):
---
{sources_text}
---

Return:
{{
  "attributed_claims": [
    {{
      "claim": "the specific factual claim",
      "event_summary": "which event supports it",
      "event_index": integer (0-based index into the sources list, or null if unattributed)
    }}
  ],
  "unattributed_claims": ["claims that could not be sourced"]
}}

Remember: each event_index should appear at most once. Skip claims with no clear source."""


# ── Out-of-scope response ─────────────────────────────────────────────────────

OUT_OF_SCOPE_RESPONSE = (
    "This system is focused on competitive market intelligence for tracked companies. "
    "I can answer questions about pricing, product features, funding, partnerships, "
    "hiring patterns, customer sentiment, and competitive strategy for: {tracked_companies}. "
    "What would you like to know about these companies?"
)

UNTRACKED_COMPANY_RESPONSE = (
    "I don't have {company} in my monitored company list, so I can't pull from "
    "the continuously-updated knowledge base. I searched the web for recent information instead. "
    "Would you like me to add {company} to the monitoring list? "
    "If so, it will be tracked starting with the next daily crawl."
)
