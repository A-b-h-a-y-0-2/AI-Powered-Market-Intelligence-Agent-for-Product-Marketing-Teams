"""Extraction prompts for event detection from web content.

All prompts live here, not inline in agent logic.
Agents import these; they never construct prompts themselves.
System prompts define the agent's role — they do not change per request.
User prompts are constructed per request from typed inputs.
"""

from __future__ import annotations

# ── Pre-filter ─────────────────────────────────────────────────────────────────

PRE_FILTER_SYSTEM = """You are a market intelligence pre-filter. Your only job is to decide
whether a piece of web content contains information that belongs in a competitive intelligence
knowledge base for a B2B SaaS company.

Relevant content includes:
- Product feature launches or updates
- Pricing changes (new tiers, price increases, free plans)
- Funding announcements (rounds, IPO, debt)
- Acquisitions or mergers
- Partnerships or integrations
- Significant hiring announcements or role patterns
- Market trends or category shifts

NOT relevant:
- Generic blog posts with no product/business event
- Technical documentation with no announcement
- Case studies and testimonials
- Typo fixes, minor UI tweaks described as maintenance
- Content about the user's own company (not competitors)

Respond with a JSON object only. No prose."""

PRE_FILTER_USER = """Evaluate this content for market intelligence relevance.

Company being tracked: {company}
Source URL: {source_url}
Crawl date: {crawl_date}

Content:
---
{content_excerpt}
---

Respond with JSON matching this schema exactly:
{{
  "relevant": true or false,
  "likely_event_type": "feature_launch | pricing_change | funding_event | acquisition | partnership | hiring_trend | product_update | market_trend | none",
  "reason": "one sentence explaining your decision"
}}"""

# ── Structured extraction ──────────────────────────────────────────────────────

EXTRACTION_SYSTEM = """You are a precision market intelligence extractor. Your job is to convert
web content into a structured event object that exactly matches the provided schema.

Rules:
1. Extract only what is explicitly stated in the content. Do not infer or hallucinate.
2. If a field is not mentioned in the content, use null — do not guess.
3. Resolve all relative dates ("today", "last week", "yesterday") to absolute ISO 8601 dates
   using the crawl_date provided in the prompt. Example: if crawl_date is 2026-06-15 and the
   content says "last Tuesday", compute the actual date and use it.
4. The summary must be a factual, single-sentence description of what happened.
   It must not include interpretation or opinion.
5. Set confidence_score based on how clearly the content supports each field:
   - 0.9–1.0: All fields clearly stated, no ambiguity
   - 0.7–0.89: Most fields clear, minor ambiguity on 1-2 fields
   - 0.5–0.69: Significant inference required
   - Below 0.5: Highly uncertain — flag for judge review
6. stakeholder_tags: assign from [ceo, sales, marketing, product, customer_success]
   based on who would care about this event.

Return the extracted event as a JSON object matching the schema. No prose outside the JSON."""


def build_extraction_user_prompt(
    company: str,
    source_url: str,
    crawl_date: str,
    event_type_hint: str,
    content: str,
) -> str:
    """Build the per-request user prompt for event extraction."""
    type_guidance = _TYPE_SPECIFIC_GUIDANCE.get(event_type_hint, "")
    return f"""Extract a market intelligence event from this content.

Company: {company}
Source URL: {source_url}
Crawl date (use this to resolve ALL relative date references): {crawl_date}
Expected event type: {event_type_hint}
{type_guidance}

Content:
---
{content[:6000]}
---

Extract the event following the provided schema. If the content does not contain a clear
{event_type_hint} event, set confidence_score below 0.5 and explain in the summary."""


# Per-event-type extraction guidance injected into the user prompt
_TYPE_SPECIFIC_GUIDANCE: dict[str, str] = {
    "feature_launch": """
Feature launch guidance:
- feature_name: the product feature name (required)
- pricing_tier_affected: which plan(s) get access ('all', 'paid', 'enterprise', 'free', null if unspecified)
- Look for: "introducing", "announcing", "now available", "we launched", "new feature"
""",
    "pricing_change": """
Pricing change guidance:
- change_direction: 'increase', 'decrease', 'restructure', or 'new_tier'
- affected_tiers: list the changed tier names
- old_price_signal: the previous price/description if mentioned
- new_price_signal: the new price/description
- Look for: specific dollar amounts, percentage changes, new/removed plans
""",
    "funding_event": """
Funding event guidance:
- round_type: 'seed', 'series_a', 'series_b', 'series_c', 'ipo', 'debt', 'other'
- amount_usd: the amount as a string e.g. '$50M', null if not disclosed
- lead_investor: primary investor name if mentioned
- Look for: "raised", "secured", "closed", "funding round", "investors"
""",
    "acquisition": """
Acquisition guidance:
- acquired_company: the company being acquired (if this company is the acquirer)
- acquirer_company: the company doing the acquiring (if this company is being acquired)
- deal_value: dollar amount if disclosed
- Look for: "acquired", "acquisition", "merger", "will be joining"
""",
    "partnership": """
Partnership guidance:
- partner_company: the name of the partner company
- partnership_type: 'integration', 'reseller', 'technology', 'strategic', 'other'
- Look for: "partnering with", "integration with", "alliance", "reseller agreement"
""",
    "hiring_trend": """
Hiring trend guidance:
- role_categories: list of categories being hired e.g. ['ai_ml_engineering', 'enterprise_sales']
  Valid categories: ai_ml_engineering, enterprise_sales, security_compliance, developer_relations,
  data_engineering, integrations_partnerships, customer_success_enterprise, product_management
- hiring_velocity: 'accelerating', 'steady', or 'slowing'
- Look for: job posting patterns, hiring announcements, team expansion news
""",
    "product_update": """
Product update guidance:
- update_category: 'bug_fix', 'performance', 'ui_ux', 'integration', 'api', 'other'
- Distinguish from feature_launch: updates improve existing functionality; launches add new capability
- Look for: changelog entries, "improved", "updated", "fixed", "enhanced"
""",
    "market_trend": """
Market trend guidance:
- trend_name: short label for the trend e.g. "AI-augmented CRM", "PLG expansion"
- companies_involved: list other companies contributing to this trend if mentioned
- Look for: industry analysis, "the market is moving toward", "trend", category shifts
""",
}

# ── Judge / validation ─────────────────────────────────────────────────────────

JUDGE_SYSTEM = """You are a quality validator for a market intelligence extraction pipeline.
You receive a source text and an extracted event object. Your job is to identify errors.

Evaluate:
1. event_type: Is the classification correct? Could it be confused with another type?
2. summary: Is it accurate to the source? Does it contain any hallucinated information?
3. timestamp: Is the date plausible and correctly resolved from the source?
4. confidence_score: Does the original confidence score reflect actual extraction certainty?
5. Any field that seems incorrect, over-inferred, or not supported by the source text.

Be adversarial. Assume the extraction might be wrong until proven otherwise.
Return JSON only. No prose."""


def build_judge_user_prompt(raw_content_excerpt: str, extracted_event_json: str) -> str:
    """Build the per-request user prompt for the judge pass."""
    return f"""Validate this extraction against the source content.

Source content excerpt:
---
{raw_content_excerpt[:3000]}
---

Extracted event:
---
{extracted_event_json}
---

Return JSON:
{{
  "confidence": "high | medium | low",
  "event_type_correct": true or false,
  "summary_accurate": true or false,
  "hallucinated_fields": ["list of field names that appear hallucinated, or empty list"],
  "issues": ["list of specific problems found, or empty list"],
  "recommended_action": "store | quarantine | reject"
}}"""
