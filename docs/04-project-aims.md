# Project Aims — Market Intelligence Agent

---

## The Core Claim This Project Must Prove

> A continuously updating, structured event knowledge base — queryable through a conversational interface — is more valuable than a web-search-augmented chatbot for market intelligence.

Everything below is in service of proving this claim. If the project ends and you cannot demonstrate this clearly, the project is incomplete regardless of what was built.

---

## Aim 1: A Working Continuous Monitoring Pipeline

**What it means**

The system runs without a human triggering it. Every 24 hours (configurable), it crawls a defined set of sources for each competitor, extracts events, deduplicates, and writes to the event store. A failure in one crawl does not halt the entire pipeline.

**What "done" looks like**

- Minimum 3 competitors tracked
- Minimum 5 source types per competitor (blog, changelog, LinkedIn, Twitter/X, news mentions)
- Pipeline runs on a schedule without manual intervention
- Failed crawls are logged, retried once, and flagged — not silently dropped
- A monitoring endpoint shows the last successful crawl per source per competitor

**Why this is non-trivial**

Websites block bots. JS-rendered pages require headless browsers. Sources go down. Rate limits exist. The reliability engineering here is as hard as the AI engineering.

---

## Aim 2: A Structured Event Knowledge Base

**What it means**

Raw crawled content is never stored. Every piece of information enters the system as a normalized event object conforming to a defined schema.

**What "done" looks like**

- All 8 event types implemented: Feature Launch, Pricing Change, Funding Event, Acquisition, Partnership, Hiring Trend, Product Update, Market Trend
- Every event has: `company`, `event_type`, `timestamp`, `summary`, `source_urls[]`, `confidence_score`, `stakeholder_tags[]`
- Events are deduplicated — the same launch does not appear 4 times because 4 sources covered it
- Events are queryable by: company, event type, date range, confidence threshold
- The event store contains at minimum 30 days of historical events before the conversational interface is considered usable

**Why this is the hardest aim**

Classification errors compound. A wrongly typed event pollutes every downstream query. The quality of this database is the quality of the product.

---

## Aim 3: Stakeholder-Differentiated Intelligence

**What it means**

Given the same event, the system produces meaningfully different insights for each stakeholder. Not just different tone — different actionable framing.

**What "done" looks like**

- 5 stakeholder profiles implemented: CEO, Sales, Marketing, Product, Customer Success
- For any event, generating insights for all 5 profiles produces outputs that a real person in each role would find useful and distinct
- Stakeholder profiles are data, not hardcoded prompts — editable without code changes
- The conversational interface allows users to select their role, and all subsequent responses are framed accordingly

**Test to run at the end**

Take a real competitor event. Generate all 5 stakeholder views. Show them to someone who works in each role. If they say "this is actually relevant to me," the aim is met. If they say "this is generic," it is not.

---

## Aim 4: A Grounded Conversational Interface

**What it means**

Users can ask natural language questions and receive answers that are:
- Accurate relative to stored events
- Grounded (not hallucinated — every claim traces to an event)
- Appropriately scoped (temporal, entity, and topic filtering work)

**What "done" looks like**

All of these example queries must produce correct, grounded responses:

| Query | What "correct" means |
|---|---|
| "What happened this week?" | Returns events from the last 7 days, scoped to the user's company context |
| "What are our competitors focusing on?" | Synthesizes event_type distribution across competitors — not a web search |
| "Which competitor is shipping fastest?" | Counts Feature Launch + Product Update events per competitor, ranked |
| "How does Competitor A compare to us?" | Cross-references events vs. the company's own product description |
| "What should the Sales team know today?" | Returns recent events tagged with Sales-relevant implications |
| "Show evidence for this insight" | Returns the exact event IDs and source URLs that produced the insight |

The last query is the most important. If users cannot inspect the evidence, the system is a black box.

---

## Aim 5: Full Source Attribution

**What it means**

Every claim in every response is traceable to one or more specific events in the knowledge base, which are in turn traceable to one or more source URLs.

**What "done" looks like**

- Every response includes a "Sources" section (event IDs + URLs)
- The system never cites a source it did not crawl
- The attribution pass runs after generation, not during — preventing citation hallucination
- Users can click through from a citation to the original source URL

**Why this matters beyond compliance**

Trust. A sales rep who can verify that "Competitor A raised prices last week" with a direct link to the competitor's pricing page will trust the system. One who receives an unverifiable claim will not.

---

## Aim 6: Observability Over the Entire Pipeline

**What it means**

You can answer these questions at any time, without digging through logs:
- Which sources failed to crawl in the last 24 hours?
- What is the extraction confidence distribution across recent events?
- How many LLM calls did last night's run make, and what did they cost?
- Which agent step failed on which input?

**What "done" looks like**

- Langfuse dashboard showing every LLM call in every pipeline run
- A crawl health view: per-source success rate over the last 7 days
- Alerting (even just a logged error with a distinct code) when crawl success rate drops below 70% for any competitor
- Cost reporting: total LLM spend per pipeline run

---

## Aim 7: Cost-Efficient Operation

**What it means**

The system should be operable at a cost that a startup would actually pay. Continuous operation should not require Sonnet-level calls for every extraction.

**What "done" looks like**

- Cheap models (Groq, Haiku) handle classification and extraction
- Expensive models (Sonnet) are called only for insight generation and complex synthesis
- Redis caching prevents re-crawling unchanged pages
- Semantic deduplication prevents redundant extractions
- Target: full daily pipeline run for 3 competitors across 5 sources each costs under $0.50 in LLM API costs

This is a measurable target. Instrument it from day one.

---

## What This Project Is Not Aiming For

These are explicit non-goals. Not because they are unimportant, but because including them would prevent the core aims from being achieved.

| Non-goal | Why excluded |
|---|---|
| Real-time monitoring (< 1 hour latency) | Requires streaming infra; overkill for market intel which changes daily, not hourly |
| Social media sentiment analysis at scale | Requires Twitter/LinkedIn API access that is cost-prohibitive; this is a v2 feature |
| Automated action-taking (send email, update CRM) | Out of scope; the output is intelligence, not workflow automation |
| Fine-tuning custom models | Not needed; prompt engineering with Instructor achieves required extraction quality |
| Multi-language support | English-first; localization is a later feature |

---

## The End State

When this project is complete, a product marketer should be able to sit down on a Monday morning, open the interface, and within 60 seconds know:

1. What the three main competitors shipped last week
2. Which of those is most relevant to a deal their sales team is working
3. What they should update in their messaging because of it
4. Exactly which blog post, changelog, or press release this intelligence came from

That is the product. Everything above is in service of making that experience possible.
