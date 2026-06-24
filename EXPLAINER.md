# Market Intelligence Agent — Complete Project Explainer

> Read this if you want to understand what this system is, how it works,  
> and how to explain it to someone else in 5 minutes or 45 minutes.

---

## Table of Contents

1. [The One-Paragraph Pitch](#1-the-one-paragraph-pitch)
2. [The Problem It Solves](#2-the-problem-it-solves)
3. [The Big Picture](#3-the-big-picture)
4. [The Two Modes of the System](#4-the-two-modes-of-the-system)
5. [The Data Journey — End to End](#5-the-data-journey--end-to-end)
6. [The Nine Agents — What Each One Does](#6-the-nine-agents--what-each-one-does)
7. [The Data Model — What Gets Stored](#7-the-data-model--what-gets-stored)
8. [The Tech Stack — Explained](#8-the-tech-stack--explained)
9. [The API Surface](#9-the-api-surface)
10. [Configuration — What You Can Change Without Code](#10-configuration--what-you-can-change-without-code)
11. [The Observability Layer](#11-the-observability-layer)
12. [How to Run It Locally](#12-how-to-run-it-locally)
13. [Project File Map](#13-project-file-map)
14. [Key Design Decisions](#14-key-design-decisions)
15. [Known Gaps and What's Next](#15-known-gaps-and-whats-next)
16. [How to Explain It to Someone in 2 Minutes](#16-how-to-explain-it-to-someone-in-2-minutes)

---

## 1. The One-Paragraph Pitch

This system is a **24/7 competitive intelligence analyst that never sleeps**. It continuously monitors competitor websites, RSS feeds, job boards, and review platforms for any company you tell it to watch. Everything it finds gets converted into structured, typed, deduplicated intelligence events stored in a database. When a product marketing manager, CEO, or sales leader asks a question — "What is McKinsey doing right now?" or "Why did BCG cut prices?" — the system retrieves the relevant evidence and generates a grounded, cited answer in 3–8 seconds.

**The core insight:** research is hard and slow; answering questions is fast if the research is already done.

---

## 2. The Problem It Solves

### The Old Way

A product marketing team at a consulting firm currently does competitive intelligence like this:

- One analyst monitors Google Alerts (misses everything important)
- Another reads competitor blog posts manually, twice a week
- Someone checks LinkedIn for hiring patterns once a month
- A deck is assembled quarterly with whatever was remembered
- By the time the deck is done, half the information is stale

**Result:** The CEO asks "What is McKinsey doing with AI?" and the answer is either "I'll find out" or a guess.

### The New Way (This System)

- **Every 30 minutes:** RSS feeds from all tracked competitors are polled for new content
- **Every night at 2am:** Firecrawl renders their websites, Tavily searches for news, Apify scrapes job boards
- **Every night at 4am:** An LLM extracts structured events from everything collected
- **Every Sunday:** Patterns are synthesized into narratives, trends, and threat scores
- **On demand:** Any question gets answered in seconds, with source citations

**Result:** "McKinsey's threat level is HIGH. Velocity score: 78/100. Driven by 3 events in the last 14 days: [feature launch cited], [partnership cited], [pricing change cited]."

---

## 3. The Big Picture

```
┌────────────────────────────────────────────────────────────────────────┐
│                                                                        │
│  WHAT COMPETITORS ARE DOING                                            │
│  (McKinsey, BCG, Bain, Deloitte, KPMG, Oliver Wyman, Accenture...)   │
│                                                                        │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ RSS / Firecrawl / Tavily / Apify / Indeed
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — COLLECTION                                        (offline) │
│  Raw web content: blog posts, press releases, job ads, reviews        │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ Groq LLM + Instructor + Pydantic
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — EXTRACTION                                        (offline) │
│  Typed events: FeatureLaunch, PricingChange, FundingEvent, etc.       │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ DBSCAN clustering, LLM synthesis
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — SYNTHESIS                                         (offline) │
│  Narratives, threat scores, hiring signals, market trends             │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ MongoDB + Supabase pgvector + Redis
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — STORAGE                                        (persistent) │
│  Queryable knowledge base, indexed by company + time + semantics      │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ LangGraph 7-node graph
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — INTELLIGENCE                                      (online)  │
│  Retrieve + personalize + generate grounded answer                    │
└───────────────────────────┬────────────────────────────────────────────┘
                            │ FastAPI + Next.js
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 6 — INTERFACE                                         (online)  │
│  Chat UI, threat dashboard, event timeline, feature matrix, admin     │
└────────────────────────────────────────────────────────────────────────┘
                            │ Langfuse + structlog + Prometheus
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 7 — OBSERVABILITY                                  (always on)  │
│  Every LLM call traced + costed. Every event logged. Metrics scraped. │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. The Two Modes of the System

Understanding this distinction is the key to understanding the whole design.

### Mode 1: Offline Research (Scheduled, Nobody Waiting)

Runs on a clock. Crawls the web, calls LLMs, writes to the database. Nobody is waiting for results; it's fine if it takes 20 minutes. Can be parallelized. Cost-optimized with cheap models where possible.

```
Every 30 min   →  RSS poll → Extraction
Daily 02:00    →  Firecrawl + Tavily + Apify → Extraction
Daily 05:00    →  Sentiment analysis on reviews
Sunday 03:00   →  Hiring signal analysis
Sunday 05:00   →  Narrative synthesis (cluster events into stories)
Sunday 06:00   →  Convergence detection (cross-competitor patterns)
Sunday 07:00   →  Threat scoring (0–100 per competitor)
```

### Mode 2: Online Query (On-Demand, Someone Waiting)

A user asked a question. They're waiting. Target: 3–8 seconds. The system must retrieve from the already-built knowledge base and generate a response. No crawling, no big LLM calls for extraction — just retrieval and generation.

```
User question → 7-node LangGraph graph → SSE streaming response
```

**Why this separation matters:** If you tried to crawl and extract at query time, every question would take 2+ minutes and cost $0.10+. By doing the research offline, queries cost fractions of a cent and take seconds.

---

## 5. The Data Journey — End to End

Here is what happens from "McKinsey posts a press release" to "user gets an answer":

```
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 1 — COLLECTION (Research Agent, nightly 02:00 UTC)           │
│                                                                     │
│  Firecrawl renders mckinsey.com/insights → raw markdown content    │
│  Tavily searches "McKinsey AI platform launch 2026" → news URLs    │
│  Google News RSS → article titles + snippets                       │
│  Apify scrapes Indeed → "McKinsey & Company" job listings          │
│                                                                     │
│  Output: list of CrawlResult objects                               │
│    { url, content (markdown), content_hash, is_changed }           │
│                                                                     │
│  Cache check: if content_hash unchanged since last crawl → SKIP    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ only changed content proceeds
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 2 — EXTRACTION (Extraction Agent, nightly 04:00 UTC)         │
│                                                                     │
│  Pass 1 — Pre-filter (Groq llama-3.1-8b-instant, CHEAP):          │
│    "Is this about a real competitor action or just generic noise?"  │
│    → NO: discard (saves 70B calls)                                 │
│    → YES: proceed                                                   │
│                                                                     │
│  Pass 2 — Extraction (Groq llama-3.3-70b-versatile + Instructor):  │
│    Structured extraction → Pydantic model (e.g. FeatureLaunchEvent)│
│    Returns: { feature_name, summary, confidence_score, ... }       │
│    Instructor auto-retries if the model returns invalid JSON        │
│                                                                     │
│  Deduplication check:                                              │
│    Embed the new event → cosine similarity vs last 7 days          │
│    If similarity > 0.88 → DUPLICATE → discard                      │
│                                                                     │
│  Confidence routing:                                               │
│    ≥ 0.7 → write to MongoDB event store + Supabase pgvector        │
│    < 0.7 → Pass 3 (judge: 70B re-evaluates)                        │
│      After judge: ≥ 0.7 → store; still < 0.7 → QUARANTINE         │
│                                                                     │
│  Output: typed event in MongoDB + embedding in pgvector            │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ events accumulate over the week
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 3 — SYNTHESIS (Sunday pipeline, 03:00–07:00 UTC)             │
│                                                                     │
│  Hiring Signal Agent: Batches Indeed postings. Detects anomalies   │
│    vs 180-day baseline. "McKinsey hiring 4× normal AI engineers     │
│    → likely AI platform launch in 4–6 months"                      │
│                                                                     │
│  Narrative Agent: DBSCAN clusters events by semantic similarity.   │
│    If 3+ events cluster together → synthesizes a NarrativeEvent:   │
│    "Enterprise AI Pivot: 5 signals in 60 days"                     │
│                                                                     │
│  Convergence Agent: Cross-competitor clustering.                   │
│    "4 of 5 firms hired AI engineers in Q2 → market-wide AI push"  │
│                                                                     │
│  Threat Scoring Agent: Per competitor, 0–100 score:                │
│    velocity (40%) + event type weight (35%) + recency decay (25%)  │
│    → tier: HIGH / MEDIUM / LOW + trend direction                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ all stored in MongoDB
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STEP 4 — QUERY (ConversationalAgent, on demand, ~3–8s)            │
│                                                                     │
│  User: "What is McKinsey's threat level this week?"                 │
│                                                                     │
│  Node 1 — Scope Detection:                                         │
│    "Is this question about a tracked competitor?" → YES            │
│                                                                     │
│  Node 2 — Company Resolution:                                      │
│    "McKinsey" → "McKinsey & Company" (canonical name)              │
│                                                                     │
│  Node 3 — Query Classification:                                    │
│    Intent: "threat_score" query                                    │
│                                                                     │
│  Node 4 — Coverage Evaluation:                                     │
│    KB has threat score from Sunday 07:00 → sufficient, not stale   │
│                                                                     │
│  Node 5 — Retrieval:                                               │
│    pgvector hybrid search: company=McKinsey + embedding similarity  │
│    Returns: top 5 most relevant events                             │
│                                                                     │
│  Node 6 — Response Generation:                                     │
│    Groq 8B generates answer personalized to stakeholder role       │
│                                                                     │
│  Node 7 — Attribution:                                             │
│    Maps each claim → specific source event → source URL            │
│    Computes overall confidence: 0.84 (source_quality × freshness…) │
│                                                                     │
│  → Streams to user via SSE                                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. The Nine Agents — What Each One Does

Think of each agent as a specialized analyst on a team. They don't talk to each other directly — they read from and write to shared storage (MongoDB + Redis + pgvector).

### Offline Agents (Scheduled)

---

#### Research Agent
**When:** Daily 02:00 UTC  
**Input:** List of competitors from `config/sources.yaml`  
**What it does:** Dispatches crawl jobs to 4 source types per competitor:

| Source | Tool | What it gets |
|--------|------|-------------|
| `rss` | feedparser | Blog posts, press releases (30min freshness) |
| `firecrawl` | Firecrawl API | JS-rendered pages (capabilities, news sections) |
| `tavily` | Tavily API | News search results, live web content |
| `apify` | Apify actors | Indeed job listings, G2/Capterra reviews |

Handles retries, circuit breakers (5 failures = skip for 1 hour), and ETag-based change detection (don't re-extract unchanged pages).

**Output:** `list[CrawlResult]` — raw markdown content, hashes, timestamps.

---

#### Extraction Agent
**When:** Daily 04:00 UTC (and immediately after every RSS poll)  
**Input:** `CrawlResult` objects (changed content only)  
**What it does:** The most complex agent. Three passes:

```
Pass 1: Pre-filter (8B model, cheap)
  → "Is this relevant to competitor strategy?" 
  → NO: discard. YES: continue.

Pass 2: Structured extraction (70B model + Instructor)
  → Extracts a typed event (FeatureLaunch, PricingChange, etc.)
  → Pydantic validates the output; Instructor retries on failure
  → Assigns confidence score 0.0–1.0

Dedup: Embeds the event → cosine sim check vs last 7 days
  → If duplicate (sim > 0.88): discard

Routing by confidence:
  ≥ 0.7 → MongoDB + pgvector (accepted)
  < 0.7 → Pass 3: judge re-evaluates
    Still < 0.7 → quarantine (human review queue)
```

**Output:** Typed events in MongoDB, embeddings in pgvector, low-confidence items in quarantine.

---

#### Sentiment Agent
**When:** Daily 05:00 UTC  
**Input:** Review content (G2, Capterra, Reddit) collected by Research Agent  
**What it does:** Aspect-Based Sentiment Analysis (ABSA). Uses a predefined taxonomy of aspects (pricing_value, onboarding, support, AI_features, etc.) so results are consistent and aggregatable over time.

**Output:** `CustomerSentimentEvent` per platform × aspect combination, with sentiment score (-1 to +1) and representative quotes.

---

#### Matrix Agent
**When:** Within 15 minutes of any `FeatureLaunch` or `ProductUpdate` event  
**Input:** The triggering event  
**What it does:** Classifies the feature into the taxonomy from `config/feature_taxonomy.yaml` and updates the living comparison matrix for that competitor. The matrix is a structured document showing which features each competitor has, at what tier, when last updated.

**Output:** Updated feature matrix document in MongoDB.

---

#### Hiring Signal Agent
**When:** Sunday 03:00 UTC  
**Input:** 60 days of Indeed/Glassdoor job postings per competitor  
**What it does:** Counts postings by role category. Compares to 180-day baseline. If a category exceeds 2× baseline → anomaly. Uses LLM to infer strategic direction from the hiring pattern:

> "McKinsey is hiring 4× normal AI engineers and 3× enterprise sales directors → likely AI platform commercialization in 4–6 months"

**Output:** `HiringSignalEvent` + `WeakSignalPrediction` per anomalous category.

---

#### Narrative Agent
**When:** Sunday 05:00 UTC  
**Input:** All events per competitor from the last 90 days  
**What it does:** Embeds all events → DBSCAN clustering on cosine distance (eps=0.15). Each cluster of 3+ events that are semantically related becomes a narrative. LLM synthesizes the cluster into a strategic story with a memorable title.

> "Enterprise AI Pivot: BCG has launched 3 AI-native consulting offers, formed 2 tech partnerships, and promoted an AI practice lead in the last 60 days."

**Output:** `NarrativeEvent` per cluster, each pointing to its constituent event IDs.

---

#### Convergence Agent
**When:** Sunday 06:00 UTC  
**Input:** Recent events across ALL competitors  
**What it does:** Cross-competitor clustering. If 3+ competitors show the same pattern (e.g., all hiring AI engineers, all announcing AI partnerships) → this is a market-wide trend, not a one-company move. Flags it as a `MarketTrendEvent`.

> "4 of 5 tracked competitors launched AI-assisted consulting services in Q2 — this is a category-wide shift."

**Output:** `MarketTrendEvent` for each detected cross-competitor pattern.

---

#### Threat Scoring Agent
**When:** Sunday 07:00 UTC  
**Input:** All events per competitor from the last 30 days  
**What it does:** Computes a 0–100 threat score for each competitor using three weighted components:

```
Score = velocity(40%) + type_weight(35%) + recency_decay(25%)

velocity:     event count z-score vs 90-day baseline
type_weight:  acquisition=3x, pricing_change=3x, feature_launch=2x, 
              funding=2x, partnership=1x, product_update=1x
recency:      exponential decay (λ=0.05) — older events count less

Tiers:
  ≥ 70 → HIGH
  ≥ 40 → MEDIUM
  < 40 → LOW
```

Generates one sentence: "McKinsey is a HIGH threat (score: 78). 3 high-weight events in 14 days, velocity accelerating."

**Output:** `ThreatScore` document per competitor.

---

#### Digest Agent
**When:** Sunday after Threat Scoring completes  
**Input:** All synthesis outputs (narratives, threats, hiring signals, trends)  
**What it does:** Generates a weekly brief personalized per stakeholder role. A CEO brief looks different from a sales brief — each stakeholder profile in `config/stakeholders.yaml` defines what they care about and their vocabulary style.

**Output:** Weekly digest document per stakeholder role.

---

### Online Agent (On Demand)

---

#### Conversational Agent
**When:** Triggered by `POST /api/v1/chat`  
**Input:** Free-text user question + optional session_id + optional stakeholder_role  
**What it does:** The 7-node LangGraph graph described in Section 5. The entire pipeline runs in 3–8 seconds. All retrieval is from the prebuilt knowledge base — no crawling happens at query time.

Special capability: if the knowledge coverage check determines the KB is stale or insufficient, it can trigger a live Tavily search to supplement the response (and notes this in the caveats).

**Output:** Streaming SSE response with answer, source citations, confidence score, and caveats.

---

#### Discovery Agent (Bonus — Used by the API)
**When:** Called by `POST /api/v1/competitors` when you add a new competitor  
**Input:** Company name + optional domain hint  
**What it does:** Automatically discovers RSS feeds, blog sections, and news sources for a new competitor. Writes them to `config/sources.yaml`. No manual YAML editing needed to onboard a new competitor.

**Output:** `CompetitorDiscovery` with discovered source URLs.

---

## 7. The Data Model — What Gets Stored

All intelligence is stored as **typed, immutable events**. Once written, an event never changes. Corrections create new events with `schema_version` incremented.

### Event Taxonomy

```
BaseEvent (common fields: company, timestamp, summary, source_urls, confidence)
├── FeatureLaunchEvent     → feature_name, pricing_tier_affected
├── PricingChangeEvent     → change_direction, affected_tiers, old/new price signals
├── FundingEvent           → round_type, amount_usd, lead_investor
├── AcquisitionEvent       → acquired_company, acquirer_company, deal_value
├── PartnershipEvent       → partner_company, partnership_type
├── HiringTrendEvent       → role_categories, hiring_velocity
├── ProductUpdateEvent     → update_category (bug_fix/perf/ui/api/integration)
└── MarketTrendEvent       → trend_name, companies_involved

Synthesis events (generated by Sunday agents):
├── CustomerSentimentEvent → platform, aspect, sentiment_score, quotes
├── HiringSignalEvent      → role_title, role_category, seniority, strategic_signal
├── NarrativeEvent         → narrative_title, summary, constituent_event_ids
├── ThreatScore            → score (0–100), tier (HIGH/MED/LOW), trend, components
├── WeakSignalPrediction   → predicted_direction, time_horizon_months
└── EnrichedFact           → fact_type, value (CEO name, HQ, employee count, etc.)
```

### Where Things Live

| Data | Store | Why |
|------|-------|-----|
| Events (all types) | MongoDB collection `events` | Document store, flexible schema, fast by company+timestamp |
| Pipeline run state | MongoDB collection `pipeline_runs` | Track what ran, when, results, cost |
| Quarantine queue | MongoDB collection `quarantine` | Low-confidence events awaiting human review |
| Feature matrices | MongoDB collection `matrices` | Structured per-competitor feature docs |
| Threat scores | MongoDB collection `threat_scores` | Latest score per competitor |
| Training examples | MongoDB collection `training_examples` | Human corrections for DSPy Phase 5 |
| Event embeddings | Supabase pgvector table `event_embeddings` | Semantic search by vector similarity |
| Crawl content hashes | Redis | Fast dedup check; 24h TTL |
| Circuit breaker state | Redis | Consecutive failure counts per source URL |
| Session history | Redis | Conversational context per session_id |
| Dedup hashes | Redis | 7-day lookback for event-level dedup |

---

## 8. The Tech Stack — Explained

### Why These Specific Choices

**Groq** (not OpenAI directly): Groq's Llama models are dramatically faster and cheaper for structured extraction. At free tier: 100K tokens/day per model. The system uses two models (8B and 70B) which gives 200K tokens/day free. For a demo or small team, this is sufficient.

**Instructor** (the secret weapon): LLMs don't reliably return valid JSON. Instructor patches the LLM client to validate the output against a Pydantic schema. If validation fails, it automatically retries with the error message fed back to the model. This transforms extraction from "sometimes works" to "always returns valid typed data."

**LangGraph** (not raw async): The conversational agent has conditional logic — what happens next depends on what the coverage check returns. LangGraph makes this branching explicit as a graph rather than tangled if-else. It also gives checkpointing: if a 7-step pipeline fails at step 5, it can resume from step 4.

**MongoDB** (not PostgreSQL for events): Events are heterogeneous — a FeatureLaunchEvent has different fields than a FundingEvent. MongoDB's document model handles this naturally. No migrations needed when adding a new event type.

**Supabase pgvector** (not Pinecone or Chroma): We already need Supabase for the free managed PostgreSQL. pgvector adds semantic search in the same service without another infra dependency. The hybrid search (company filter + cosine similarity) is a single SQL query.

**Redis** (not in-memory dict): State needs to survive restarts. Redis also enables the circuit breaker to track failures across process restarts.

**APScheduler** (not Celery): This is a single-process application. APScheduler runs async jobs in the same event loop as FastAPI. No message queue, no worker processes, no broker. Simple, correct for the scale.

**fastembed** (local fallback): If OpenAI embeddings are unavailable (no key, quota exceeded), fastembed runs the BAAI/bge-small-en-v1.5 model locally with no API calls. 384 dimensions vs 1536, but perfectly adequate for similarity search.

### The Full Stack Table

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | 3.11+ |
| LLM (extraction) | Groq llama-3.3-70b-versatile | — |
| LLM (synthesis, chat) | Groq llama-3.1-8b-instant | — |
| LLM (structured output) | Instructor | ≥1.3 |
| Schema validation | Pydantic v2 | ≥2.7 |
| Graph orchestration | LangGraph | ≥0.2 |
| Web crawling | Firecrawl | ≥1.0 |
| Web search | Tavily | ≥0.3 |
| Job/review scraping | Apify | ≥3.0 |
| RSS parsing | feedparser | ≥6.0 |
| Event store | MongoDB + Motor (async) | ≥4.7 / ≥3.4 |
| Vector store | Supabase pgvector | ≥2.4 |
| Cache | Redis | ≥5.0 |
| Embeddings (cloud) | OpenAI text-embedding-3-small | — |
| Embeddings (local) | fastembed BAAI/bge-small | — |
| Clustering | scikit-learn DBSCAN | ≥1.4 |
| API server | FastAPI + uvicorn | ≥0.111 |
| Scheduling | APScheduler | ≥3.10 |
| Frontend | Next.js 16 + React 19 | — |
| Styling | Tailwind CSS v4 | — |
| Observability | Langfuse v3 | ≥2.25 |
| Logging | structlog | ≥24.1 |
| Metrics | Prometheus client | ≥0.20 |
| Package manager | uv | — |
| Linter | ruff | ≥0.4 |

---

## 9. The API Surface

The FastAPI server runs on port 8000. Swagger docs at `http://localhost:8000/docs`.

All routes are prefixed with `/api/v1`.

### Intelligence Routes (Read)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/events` | List events for a competitor. Filter by type, days, confidence, stakeholder tag. |
| `GET` | `/threats` | Threat scores for all competitors (pre-computed Sunday morning). |
| `GET` | `/threats/{company}` | Threat score for one company: score, tier, trend, contributing events. |
| `GET` | `/matrix/{company}` | Living feature comparison matrix for a competitor. |

### Chat Route (Streaming)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Send a question, receive a streaming SSE response. Body: `{message, session_id?, stakeholder_role?}`. |

Each SSE event is a JSON line. The stream ends with a `done` event containing the full answer, sources, and confidence score.

### Pipeline Routes (Ops)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/pipeline/status` | List recent pipeline runs with status + cost + event count. |
| `GET` | `/pipeline/status/{run_id}` | Status of a specific run. |
| `POST` | `/pipeline/trigger` | Manually trigger a pipeline run (full/rss/tavily). Returns `run_id` immediately; runs in background. |
| `GET` | `/pipeline/logs/{run_id}` | **SSE stream** of live log lines for a running pipeline. Connect immediately after triggering. |

### Admin Routes (Quarantine Review)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/quarantine` | List pending quarantined events (low-confidence extractions). |
| `PATCH` | `/admin/quarantine/{id}` | Review: `approve`, `correct` (with field corrections), or `reject`. |
| `GET` | `/admin/quarantine/stats` | Counts by status + correction rate per event type. |

### Configuration Routes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/competitors` | Add new competitors. Auto-discovers their sources; updates `config/sources.yaml`. |
| `POST` | `/competitors/{name}/rediscover` | Force fresh source discovery for an existing competitor. |

### Observability Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Infrastructure health: MongoDB, Redis, Supabase status. |
| `GET` | `/metrics` | Prometheus metrics (scrape this with Prometheus). |

---

## 10. Configuration — What You Can Change Without Code

### Adding a Competitor

Edit `config/sources.yaml`. Or call `POST /api/v1/competitors` and it's done automatically.

Manual YAML entry looks like:
```yaml
competitors:
  - competitor: "Your Competitor Name"
    canonical_names:
      - "Your Competitor Name"
      - "Competitor"         # all known aliases — used for entity resolution
    sources:
      - type: rss
        frequency: 30min
        url: https://competitor.com/blog/rss
      - type: firecrawl
        frequency: daily
        url: https://competitor.com/newsroom
      - type: tavily
        frequency: daily
        query: "Competitor Name product launch announcement 2026"
      - type: apify
        frequency: weekly
        actor: indeed-jobs-scraper
        max_results: 5
        query: "Competitor Name"
```

The Research Agent picks this up on the next scheduled run. Zero code changes.

### Changing Which LLM to Use

Edit `config/models.yaml`:
```yaml
routing:
  pre_filter: "llama-3.1-8b-instant"       # cheapest model for yes/no filter
  extraction: "llama-3.3-70b-versatile"    # best model for structured extraction
  synthesis: "llama-3.1-8b-instant"        # narratives, threat scoring
  conversational: "llama-3.1-8b-instant"   # user-facing answers
```

Swap `"llama-3.3-70b-versatile"` for `"anthropic/claude-sonnet-4-6"` (via OpenRouter) without touching any agent code.

### Changing Confidence Thresholds

Edit `config/thresholds.yaml`:
```yaml
confidence:
  quarantine_below: 0.70      # events below this go to quarantine
  dedup_merge_above: 0.88     # cosine similarity above which events are duplicates
```

Lower `quarantine_below` to 0.6 to accept more events with less certainty. Raise it to 0.8 for stricter quality.

### Changing Threat Score Weights

```yaml
threat_scoring:
  velocity_weight: 40    # how fast they're moving (event count vs baseline)
  type_weight: 35        # what type of moves (acquisition > feature launch)
  recency_weight: 25     # older events count less
  event_type_multipliers:
    pricing_change: 3    # most threatening
    acquisition: 3
    feature_launch: 2
    funding_event: 2
    partnership: 1
    product_update: 1
```

### Changing Narrative Clustering Sensitivity

```yaml
narrative:
  min_cluster_size: 3       # need at least 3 events to make a narrative
  dbscan_eps: 0.15          # cosine distance threshold — lower = tighter clusters
  lookback_days: 90         # how far back to look for events to cluster
```

Lower `dbscan_eps` to 0.10 for tighter, more specific narratives. Raise it to 0.25 to cluster more loosely related events together.

---

## 11. The Observability Layer

### Every LLM Call Is Traced

No LLM call runs without a Langfuse trace span. The pattern is:
```python
async with trace_span("extraction_agent", "extract_feature_launch", run_id=run_id) as span:
    result = await llm_call(...)
    span.record_llm(
        model="llama-3.3-70b-versatile",
        input_tokens=512,
        output_tokens=128,
        cost_usd=0.0004,
    )
```

In Langfuse you see: which agent called which model, when, how many tokens, how much it cost, how long it took, whether it succeeded.

### Every Log Line is a JSON Object

```json
{
  "timestamp": "2026-06-24T02:34:12Z",
  "level": "info",
  "agent": "extraction_agent",
  "action": "extract_event",
  "source": "https://mckinsey.com/insights/article",
  "event_type": "feature_launch",
  "confidence_score": 0.91,
  "cost_usd": 0.0008,
  "duration_ms": 1240,
  "run_id": "pipeline_20260624_0200_a3f8b2c1"
}
```

Fields are consistent across all agents. This means you can query logs with any log aggregator (Datadog, Splunk, Elastic, or just `jq`).

Special behavior: any log line that includes `run_id` is automatically forwarded to the SSE live stream at `/api/v1/pipeline/logs/{run_id}`. This is how the pipeline log streaming works — the logger itself does it.

### Named Error Codes

Every known failure mode has a machine-readable code:

| Category | Codes |
|----------|-------|
| Crawl | `CRAWL_FAILED`, `CRAWL_BLOCKED`, `CRAWL_TIMEOUT`, `CRAWL_CONTENT_UNCHANGED` |
| Extraction | `EXTRACTION_INVALID_SCHEMA`, `EXTRACTION_LOW_CONFIDENCE`, `EXTRACTION_PREFILTER_IRRELEVANT` |
| Storage | `STORE_WRITE_FAILED`, `STORE_READ_FAILED`, `STORE_CONNECTION_FAILED` |
| Circuit | `CIRCUIT_OPEN` (source is being avoided after 5 consecutive failures) |
| LLM | `LLM_CALL_FAILED`, `LLM_RATE_LIMITED`, `LLM_CONTEXT_TOO_LONG` |
| Query | `QUERY_OUT_OF_SCOPE`, `COMPANY_NOT_TRACKED`, `COVERAGE_INSUFFICIENT` |

These codes are filterable in Langfuse and any log aggregator. An alert for `CIRCUIT_OPEN` tells you a source is broken. An alert for `EXTRACTION_LOW_CONFIDENCE` spike tells you content quality dropped.

### Prometheus Metrics

Scraped at `/metrics`. Covers crawl health: success/failure rates per source, content change rates, extraction throughput.

---

## 12. How to Run It Locally

### Prerequisites

- Python 3.11+
- `pip install uv` (package manager)
- MongoDB running locally (`mongodb://localhost:27017`) or Atlas free tier
- Redis running locally (`redis://localhost:6379`) or Upstash free tier
- Supabase project (free tier works) — needed for vector search
- Groq API key (free tier: 100K tokens/day per model)
- Langfuse account (free tier works) — for LLM tracing

Optional but recommended:
- Tavily API key (free tier: 1000 searches/month)
- Firecrawl API key (free tier available)

### Step-by-Step

```bash
# 1. Clone and install
git clone <repo-url>
cd AI-Powered-Market-Intelligence-Agent-for-Product-Marketing-Teams
uv sync

# 2. Set up environment
cp .env.example .env
# Edit .env and fill in:
# GROQ_API_KEY=...
# OPENROUTER_API_KEY=...
# LANGFUSE_PUBLIC_KEY=...
# LANGFUSE_SECRET_KEY=...
# MONGODB_URI=mongodb://localhost:27017
# SUPABASE_URL=https://your-project.supabase.co
# SUPABASE_SERVICE_KEY=...
# REDIS_URL=redis://localhost:6379

# 3. Run the Supabase migration (one-time only)
# Open your Supabase dashboard → SQL Editor
# Paste and run: storage/migrations/001_vector_store.sql

# 4. Verify everything is connected
uv run python scripts/smoke_test.py

# 5a. Run the full pipeline (terminal dashboard, good for demos)
uv run python scripts/live_pipeline.py

# 5b. OR start the full server (API + scheduler)
uv run python main.py
# API at http://localhost:8000
# Swagger docs at http://localhost:8000/docs

# 6. Run the frontend (optional)
cd frontend
npm install
npm run dev
# UI at http://localhost:3000

# 7. Run the test suite
cd ..
uv run pytest
```

### What Happens at Startup (main.py)

When you run `uv run python main.py`, the startup sequence is:

```
1. Load .env → AppConfig (validates all required keys)
2. Configure structlog JSON logger
3. Initialize Langfuse tracer
4. Load config/sources.yaml → competitor list
5. Load config/models.yaml → model routing + costs
6. Connect Redis, MongoDB, Supabase, (optional Tavily, Apify)
7. Instantiate all 13 agents (dependency-injected)
8. Build LangGraph pipelines (Research+Extraction, Sunday Supervisor)
9. Health check all agents
10. Start APScheduler (4 jobs: RSS 30min, daily 02:00, daily 05:00, Sunday 03:00)
11. Start FastAPI/uvicorn on port 8000
```

If any required connection fails (MongoDB, Redis), startup aborts with a clear error.

---

## 13. Project File Map

```
Root
├── main.py                    ← THE entry point. Read this first.
├── pyproject.toml             ← Dependencies + build config + linter settings
├── .env.example               ← Copy to .env and fill in your keys
│
├── agents/                    ← One file per agent, single responsibility
│   ├── base.py                ← Abstract BaseAgent[InputT, OutputT] — the contract
│   ├── research_agent.py      ← Crawls + dispatches to 4 source types
│   ├── extraction_agent.py    ← 3-pass: pre-filter → extract → judge
│   ├── sentiment_agent.py     ← ABSA on reviews
│   ├── matrix_agent.py        ← Updates feature comparison matrix
│   ├── hiring_signal_agent.py ← Job postings → strategic predictions
│   ├── narrative_agent.py     ← Cluster events → strategic stories
│   ├── convergence_agent.py   ← Cross-competitor market trends
│   ├── threat_scoring_agent.py← 0–100 threat score per competitor
│   ├── digest_agent.py        ← Weekly brief per stakeholder role
│   ├── intelligence_agent.py  ← On-demand personalized insights
│   ├── conversational_agent.py← 7-node Q&A graph (the main product)
│   ├── discovery_agent.py     ← Auto-finds sources for new competitors
│   └── dspy_optimizer.py      ← Phase 5: self-improving prompts (future)
│
├── api/
│   ├── app.py                 ← FastAPI factory; injects deps into app.state
│   └── routes.py              ← All 16 routes (read this to see the full API)
│
├── pipeline/
│   ├── research_extraction_graph.py  ← LangGraph: research → extraction → checkpoint
│   └── supervisor.py                 ← Sunday: hiring → narrative → convergence → threat
│
├── schemas/
│   ├── events.py              ← All 14 event types + AnyEvent union (READ THIS SECOND)
│   ├── state.py               ← CrawlResult, ExtractionResult, PipelineState, QuarantinedEvent
│   └── config.py              ← AppConfig, CompetitorConfig, all threshold models
│
├── prompts/
│   ├── extraction.py          ← Pre-filter + extraction + judge prompt functions
│   ├── sentiment.py           ← ABSA prompts + aspect taxonomy
│   ├── narrative.py           ← Narrative synthesis + convergence prompts
│   ├── intelligence.py        ← Stakeholder insight prompts
│   └── conversational.py      ← 7 node prompts (scope → attribution)
│
├── storage/
│   ├── event_store.py         ← MongoDB CRUD for all collection types
│   ├── vector_store.py        ← Supabase pgvector: embed + upsert + hybrid search
│   ├── cache.py               ← Redis: crawl cache, sessions, dedup, circuit breaker
│   ├── graph_store.py         ← MongoDB $graphLookup for causal chain queries
│   └── migrations/
│       └── 001_vector_store.sql  ← Creates event_embeddings table (run once in Supabase)
│
├── tools/
│   ├── crawler.py             ← Firecrawl + ETag change detection + circuit breaker
│   ├── rss_crawler.py         ← RSS/Atom parsing (feedparser)
│   ├── search.py              ← Tavily wrapper
│   ├── embedder.py            ← OpenAI / custom / local fastembed with auto-fallback
│   ├── llm_adapter.py         ← Groq + OpenRouter unified client (instructor-patched)
│   ├── apify.py               ← Apify actor client for job boards + reviews
│   └── errors.py              ← 40+ named error codes + typed exception hierarchy
│
├── observability/
│   ├── logger.py              ← structlog: JSON in prod, console in dev
│   ├── tracing.py             ← Langfuse v3 span context manager + cost calculator
│   └── metrics.py             ← Prometheus metrics for crawl health
│
├── config/                    ← Data files — change these, never the code
│   ├── sources.yaml           ← 8 competitors + all their crawl sources
│   ├── models.yaml            ← LLM routing + per-model costs + retry config
│   ├── thresholds.yaml        ← Confidence, freshness, circuit breaker, DBSCAN params
│   ├── stakeholders.yaml      ← What each role cares about + vocabulary style
│   └── feature_taxonomy.yaml  ← Feature category taxonomy for the matrix
│
├── scripts/
│   ├── live_pipeline.py       ← 12-stage pipeline with Rich terminal dashboard (demo)
│   ├── full_pipeline_demo.py  ← 3-competitor research → extraction → query
│   ├── smoke_test.py          ← Infrastructure connectivity check
│   └── minimal_smoke_test.py  ← Storage-only check
│
├── tests/
│   ├── conftest.py            ← Shared fixtures
│   ├── test_schemas.py        ← Validates all 14+ Pydantic models
│   ├── test_extraction_agent.py  ← Schema contracts (mocked LLM)
│   ├── test_rss_crawler.py    ← Hash uniqueness, parsing
│   ├── test_threat_scoring.py ← Threat score math
│   ├── test_errors.py         ← Error code coverage
│   └── test_narrative_agent.py  ← Clustering + synthesis (mocked)
│
└── frontend/
    └── app/
        ├── chat/              ← SSE streaming chat interface
        ├── dashboard/         ← Threat scoring overview (ThreatCard component)
        ├── events/            ← Event timeline per competitor
        ├── matrix/            ← Feature comparison matrix view
        └── admin/             ← Quarantine review queue
```

---

## 14. Key Design Decisions

These are the deliberate choices that make this system work. Understanding them helps you make changes safely.

### Decision 1: Schema First

Every data structure is defined in `schemas/` as a Pydantic model *before* any agent code touches it. When adding a new event type, the sequence is:
1. Add the Pydantic model to `schemas/events.py`
2. Add it to the `AnyEvent` union
3. Add handling to `ExtractionAgent`
4. Add tests to `tests/test_schemas.py`

If you skip step 1, nothing else works.

### Decision 2: Prompts Are Not Inline Strings

Every LLM prompt lives in `prompts/*.py` as a function that accepts parameters and returns a string. No f-strings inside agent methods. This means:
- Prompt changes don't require reading agent logic
- Prompts are testable without the full agent
- Multiple agents can share the same prompt builder

### Decision 3: Structured Output Only

The system uses Instructor on every LLM call. The model is never allowed to return free text that the code then parses. The model must return a valid Pydantic object or Instructor retries. This eliminates an entire class of bugs.

### Decision 4: Events Are Immutable

Once a `FeatureLaunchEvent` is written to MongoDB, it is never updated. If a human corrects it (via the quarantine UI), a new event is created with `human_reviewed=True` and the corrected fields. The original is preserved. This enables:
- Full audit trail
- Training data collection for DSPy
- No race conditions on writes

### Decision 5: Agents Don't Call Each Other

Agents communicate via shared storage, not by calling each other's methods. `NarrativeAgent` reads from MongoDB; it doesn't call `ExtractionAgent.run()`. This means:
- Any agent can fail without cascading failures
- Agents can run independently for testing
- The order of Sunday synthesis is a scheduling concern, not a code dependency

### Decision 6: Configuration Is Data

Competitor list, model routing, confidence thresholds, feature taxonomy — all of it is YAML. The code reads from these files at startup. Changing a model, adding a competitor, or adjusting a threshold requires a file edit and a restart, not a code change and a deploy.

### Decision 7: Graceful Degradation

Every optional dependency (Firecrawl, Tavily, Apify, OpenAI embeddings, Langfuse) is optional at the dependency injection level. Missing keys produce logged warnings, not crashes. The system runs with only Groq + MongoDB + Redis.

---

## 15. Known Gaps and What's Next

### Security Gaps (Pre-Production Blockers)

| Gap | Risk | Fix |
|-----|------|-----|
| No authentication on any endpoint | Anyone can trigger pipelines, approve quarantine items, add competitors | Add API key middleware or OAuth |
| CORS `allow_origins=["*"]` | Any website can call the API | Restrict to known frontend origins via env var |
| No rate limiting on `/chat` | Groq costs spike under load | Add FastAPI rate limiter |
| `sources.yaml` written by API without input sanitization | Path traversal risk if company name contains `../` | Sanitize company names before YAML write |

### Coverage Gaps (Tests)

- No tests for `ConversationalAgent` (complex LangGraph graph)
- No integration tests against real MongoDB/Supabase/Redis
- No tests for `api/routes.py` (no FastAPI TestClient tests)
- No end-to-end tests
- 8 of 13 agents have no unit tests

### Planned Phases

| Phase | What It Adds |
|-------|-------------|
| Phase 5 (partially built) | DSPy self-improving extraction: 50+ human corrections → weekly prompt optimization → higher extraction accuracy automatically |
| Not yet started | Dockerfile + docker-compose for reproducible deployment |
| Not yet started | CI/CD pipeline (GitHub Actions) |
| Not yet started | Frontend completion (current pages are partially stubbed) |

---

## 16. How to Explain It to Someone in 2 Minutes

### For a Non-Technical Stakeholder

> "Imagine you hired a team of analysts who work 24/7 to monitor your top 8 competitors. Every 30 minutes they check for new blog posts. Every night they read through everything and write up summaries in a structured format. Every Sunday they prepare threat assessments and strategic briefings.
>
> Now instead of waiting for the weekly deck, any person on your team can just type a question — 'What is McKinsey doing with AI?' — and get a cited, sourced answer in 5 seconds.
>
> That's what this system does, except the analysts are AI agents and it costs fractions of a cent per question."

### For a Technical Interviewer

> "It's a multi-agent AI system with two distinct runtime modes. An offline batch mode runs on a scheduler — RSS polls every 30 minutes, Firecrawl/Tavily/Apify daily, synthesis Sunday — to build a structured knowledge base in MongoDB and pgvector. An online query mode uses a 7-node LangGraph conversational agent to answer questions from that prebuilt KB in 3–8 seconds.
>
> The extraction pipeline uses a 3-pass approach: cheap 8B pre-filter, expensive 70B structured extraction with Instructor + Pydantic, and a conditional judge pass only when confidence is below 0.7. Deduplication uses embedding cosine similarity. Everything below 0.7 goes to a human quarantine review queue that feeds a future DSPy self-improvement loop.
>
> The architecture is schema-first, config-driven (competitors/models/thresholds are YAML), and event-immutable — corrections create new events rather than mutating old ones."

### For a Product Manager

> "Users can ask any question about competitors they're tracking — threat levels, feature launches, pricing changes, hiring signals, strategic narratives — and get answers grounded in evidence, with source URLs, in under 10 seconds.
>
> Adding a new competitor takes 30 seconds: call one API endpoint with the company name, and the system auto-discovers their RSS feeds and web pages. No developer needed.
>
> The system tracks 8 consulting competitors out of the box, but the whole competitor list is a YAML file you can edit."

---

*This document reflects the codebase as of the initial commit (June 2026, version 0.1.0).*  
*Source of truth: the code itself. If this doc conflicts with the code, trust the code.*
