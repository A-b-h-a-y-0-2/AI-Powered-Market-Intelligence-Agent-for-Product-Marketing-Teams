# AI-Powered Market Intelligence Agent

A production-grade, multi-agent system that continuously monitors competitors, converts raw web content into structured knowledge, and answers any stakeholder query in under 10 seconds — grounded in evidence, with source citations and confidence scores on every response. 


(⚠️ Demo project — add authentication before any production use)


Built for management consulting firms and product marketing teams who need to know what competitors are doing before it shows up in the news.
---

## What It Does

**Research is offline. Answers are online.**

A scheduled pipeline runs every night, crawling competitor websites, RSS feeds, press releases, job boards, and review platforms. Everything it finds gets converted into typed, deduplicated, versioned events stored in MongoDB and indexed in a pgvector knowledge base.

When a stakeholder asks a question, the Conversational Agent retrieves the relevant events from the knowledge base, generates a grounded answer, and cites the exact source URL behind every claim — in 3–8 seconds.

---

## The Seven Demo Moments

| # | What You Ask | What You Get |
|---|---|---|
| 1 | "What is McKinsey's threat level this week?" | HIGH / MEDIUM / LOW with a velocity score, trend direction, and the 3 events that drove it |
| 2 | "Show me BCG's feature gap vs us" | A structured table, every cell traced to a source event |
| 3 | "What is Bain's strategy right now?" | "Enterprise AI pivot: 5 signals over 60 days" — expandable to constituent events |
| 4 | "What will Deloitte do next?" | "Based on 7 AI engineering + 4 enterprise sales hires in Q2, likely an AI platform launch in 4–6 months" |
| 5 | "What do McKinsey's clients actually think of their new pricing?" | ABSA summary with G2 quotes, sentiment score per aspect |
| 6 | "Why did Accenture cut prices?" | Evidence chain: margin pressure (March) → churn signals on Reddit (April) → price cut (May). Every step sourced. |
| 7 | "What is the whole market doing?" | "4 of 5 tracked competitors launched AI-assisted services in Q2 — this is a category-wide shift" |

---

## Architecture

```
Layer 1  COLLECTION    Web → Raw Content          (scheduled, offline)
Layer 2  EXTRACTION    Raw Content → Events        (scheduled, offline)
Layer 3  SYNTHESIS     Events → Narratives+Signals (scheduled, offline)
Layer 4  STORAGE       Queryable Knowledge Base    (persistent)
Layer 5  INTELLIGENCE  Events + Persona → Insights (on-demand)
Layer 6  INTERFACE     Query → Grounded Answer     (online, ~3–8s)
Layer 7  OBSERVABILITY Everything logged, traced, costed
```

### The Nine Agents

| Agent | Schedule | What It Does |
|---|---|---|
| **Research Agent** | Daily 02:00 | Dispatches crawl jobs across RSS, Firecrawl, Tavily, and Apify. Handles retries and circuit breakers per source. |
| **Extraction Agent** | Daily 04:00 | Three-pass pipeline: pre-filter (cheap) → structured extraction (Groq + Instructor + Pydantic) → judge pass (only on low confidence). Deduplicates by embedding cosine similarity. |
| **Sentiment Agent** | Daily 05:00 | Aspect-based sentiment analysis on G2/Capterra/Reddit reviews. Predefined aspect taxonomy so results are consistent and aggregatable. |
| **Matrix Agent** | Event-triggered | Fires within 15 minutes of any FeatureLaunch or ProductUpdate event. Classifies the feature into the taxonomy and updates the living comparison matrix. |
| **Hiring Signal Agent** | Sunday 03:00 | Batches Indeed/Glassdoor job postings. Flags anomalous categories vs. 180-day baseline. Infers strategic direction from hiring patterns. |
| **Narrative Agent** | Sunday 05:00 | Clusters related events per competitor (DBSCAN). Synthesizes each cluster into a strategic story with a memorable title. |
| **Convergence Agent** | Sunday 06:00 | Cross-competitor clustering. If 3+ companies show the same pattern, flags a market-wide trend. |
| **Threat Scoring Agent** | Sunday 07:00 | Scores each competitor 0–100 on velocity + event type weighting + recency decay. Returns tier (HIGH/MEDIUM/LOW) and trend direction. |
| **Conversational Agent** | On-demand | 7-node graph: scope detection → company resolution → query classification → knowledge coverage evaluation → retrieval → response generation → attribution pass → confidence assembly. |

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM — extraction | Groq (`llama-3.3-70b-versatile`) + Instructor + Pydantic v2 |
| LLM — synthesis / conversational | Groq (`llama-3.1-8b-instant`) |
| LLM — validation (conditional) | Groq 70B (only when extraction confidence < 0.7) |
| Web crawling | Firecrawl (JS-rendered pages), Tavily (search + live fallback) |
| Job postings | Apify (`indeed-jobs-scraper`) |
| Orchestration | LangGraph with checkpointed state |
| Event store | MongoDB (Motor async) |
| Vector store | Supabase pgvector — hybrid retrieval (company + timestamp filter + cosine) |
| Cache + sessions | Redis |
| Embeddings | fastembed local (BAAI/bge-small-en-v1.5, 384-dim) or OpenAI |
| API | FastAPI with streaming SSE |
| Frontend | Next.js |
| Observability | Langfuse (every LLM call traced + costed), structlog (JSON logs) |
| Scheduling | APScheduler |
| Package manager | uv |

---

## Project Structure

```
.
├── agents/                    # One file per agent — single responsibility
│   ├── base.py                # BaseAgent: health check, name, tracing hooks
│   ├── research_agent.py      # LangGraph research pipeline (Firecrawl + Tavily + Apify)
│   ├── extraction_agent.py    # Three-pass extraction with Instructor + Pydantic
│   ├── sentiment_agent.py     # ABSA on product reviews
│   ├── matrix_agent.py        # Living feature comparison matrix
│   ├── hiring_signal_agent.py # Job posting analysis → weak signal predictions
│   ├── narrative_agent.py     # Event clustering → strategic story detection
│   ├── convergence_agent.py   # Cross-competitor market trend detection
│   ├── threat_scoring_agent.py# Velocity + type + recency → 0–100 threat score
│   ├── digest_agent.py        # Weekly brief per stakeholder role
│   ├── intelligence_agent.py  # On-demand stakeholder-specific insights
│   ├── conversational_agent.py# 7-node query-answer graph with attribution
│   └── dspy_optimizer.py      # Self-improving extraction (Phase 5)
│
├── tools/                     # One file per tool category
│   ├── crawler.py             # Firecrawl + circuit breaker + ETag change detection
│   ├── search.py              # Tavily wrapper with typed results
│   ├── embedder.py            # fastembed (local) or OpenAI embeddings
│   ├── llm_adapter.py         # Groq + OpenRouter unified adapter
│   ├── rss_crawler.py         # RSS/Atom feed polling
│   ├── apify.py               # Apify actor client (Indeed, G2, LinkedIn)
│   └── errors.py              # Named error codes + typed exceptions
│
├── schemas/                   # All Pydantic models — schema first, code second
│   ├── events.py              # BaseEvent + 8 event types + synthesis schemas
│   ├── state.py               # LangGraph pipeline state (fully serializable)
│   └── config.py              # AppConfig, SourceConfig, CompetitorConfig
│
├── prompts/                   # All prompt templates — never inline in agents
│   ├── extraction.py          # Pre-filter + extraction + judge prompts
│   ├── narrative.py           # Narrative synthesis + convergence + hiring signal
│   ├── intelligence.py        # Stakeholder insight generation
│   ├── conversational.py      # 7 node prompts (scope → attribution)
│   └── sentiment.py           # ABSA prompts with predefined aspect taxonomy
│
├── storage/                   # Storage layer — agents return typed objects, this layer writes
│   ├── event_store.py         # MongoDB: events, entity graph, matrix, quarantine, enriched facts
│   ├── vector_store.py        # Supabase pgvector: embed + upsert + hybrid search
│   ├── cache.py               # Redis: crawl cache, session store, dedup hashes
│   ├── graph_store.py         # MongoDB $graphLookup for causal chain queries
│   └── migrations/            # SQL migrations for Supabase pgvector schema
│
├── pipeline/                  # LangGraph graph definitions
│   ├── research_extraction_graph.py  # Checkpointed research → extraction pipeline
│   └── supervisor.py          # Sunday synthesis supervisor (sequential dispatch)
│
├── api/                       # FastAPI — input validation + agent call + format output only
│   ├── app.py                 # FastAPI app factory
│   └── routes.py              # /chat (SSE), /threats, /matrix, /narratives, /events
│
├── frontend/                  # Next.js UI
│   └── app/
│       ├── chat/              # Conversational interface with streaming
│       ├── dashboard/         # Threat scoring overview
│       ├── events/            # Event timeline per competitor
│       ├── matrix/            # Feature comparison matrix
│       └── admin/             # Quarantine review queue
│
├── observability/
│   ├── logger.py              # structlog JSON logger configuration
│   ├── tracing.py             # Langfuse span helpers (trace_span context manager)
│   └── metrics.py             # Prometheus metrics for crawl health
│
├── config/                    # Configuration data — changing these never requires code changes
│   ├── sources.yaml           # Competitor registry with canonical names + all crawl sources
│   ├── models.yaml            # Model routing table + per-model costs + retry config
│   ├── stakeholders.yaml      # Stakeholder profiles (cares_about, vocabulary_style)
│   ├── feature_taxonomy.yaml  # Feature category taxonomy for the matrix
│   └── thresholds.yaml        # Confidence thresholds, freshness windows, circuit breaker params
│
├── scripts/                   # Runnable demos and smoke tests
│   ├── live_pipeline.py       # Full 12-stage pipeline with Rich live dashboard
│   ├── full_pipeline_demo.py  # 3-competitor research → extraction → query demo
│   ├── smoke_test.py          # Fast connectivity smoke test (no LLM calls)
│   └── minimal_smoke_test.py  # Storage-only health check
│
├── tests/
│   ├── test_extraction_agent.py
│   ├── test_narrative_agent.py
│   ├── test_threat_scoring.py
│   ├── test_schemas.py
│   ├── test_rss_crawler.py
│   └── test_errors.py
│
├── docs/
│   ├── 01-technology-choices.md
│   ├── 02-research-topics.md
│   ├── 03-sota-advancements.md
│   └── 04-project-aims.md
│
├── main.py                    # Entry point: wires all agents + schedules + starts FastAPI
├── pyproject.toml             # uv/hatch project config
├── uv.lock
└── .env.example
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- MongoDB (local or Atlas)
- Redis (local or Upstash)
- Supabase project (free tier works)
- Groq API key (free tier: 100K tokens/day per model)
- Tavily API key (free tier: 1K searches/month)

### 1. Clone and install

```bash
git clone https://github.com/your-username/market-intelligence-agent.git
cd market-intelligence-agent
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```env
# Required
GROQ_API_KEY=your_key
TAVILY_API_KEY=your_key
MONGODB_URI=mongodb://localhost:27017
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_key
REDIS_URL=redis://localhost:6379

# Optional — for Langfuse cost tracking
LANGFUSE_PUBLIC_KEY=your_key
LANGFUSE_SECRET_KEY=your_key
LANGFUSE_HOST=https://us.cloud.langfuse.com
```

### 3. Run the Supabase migration

The vector store needs one SQL migration to create the `event_embeddings` table and the `match_event_embeddings` RPC function:

```bash
# Run the SQL in storage/migrations/001_vector_store.sql
# via the Supabase SQL editor or Management API
```

### 4. Verify infrastructure

```bash
uv run python scripts/smoke_test.py
```

### 5. Run the full live pipeline (with dashboard)

```bash
uv run python scripts/live_pipeline.py
```

This runs all 12 stages with a real-time Rich dashboard showing agent status, KB metrics, and a scrolling event log.

### 6. Run the API server

```bash
uv run python main.py
```

The FastAPI server starts on `http://localhost:8000`. Swagger docs at `/docs`.

### 7. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend at `http://localhost:3000`.

---

## Configuration

### Adding a competitor

Edit [config/sources.yaml](config/sources.yaml):

```yaml
competitors:
  - competitor: "Competitor Name"
    canonical_names:
      - "Competitor Name"
      - "Competitor"        # aliases for entity resolution
    sources:
      - type: rss
        frequency: 30min
        url: https://competitor.com/blog/rss
      - type: firecrawl
        frequency: daily
        url: https://competitor.com/newsroom
      - type: tavily
        frequency: daily
        query: "Competitor Name product launch announcement 2025"
      - type: apify
        frequency: weekly
        actor: indeed-jobs-scraper
        max_results: 5
        query: "Competitor Name"
```

The Research Agent picks this up on the next scheduled run. No code changes required.

### Model routing

Edit [config/models.yaml](config/models.yaml) to change which model handles which task:

```yaml
routing:
  pre_filter: "llama-3.1-8b-instant"      # cheap classifier
  extraction: "llama-3.3-70b-versatile"   # best structured extraction quality
  synthesis: "llama-3.1-8b-instant"       # narratives, threat scoring
  conversational: "llama-3.1-8b-instant"  # user-facing responses
```

### Confidence thresholds

Edit [config/thresholds.yaml](config/thresholds.yaml):

```yaml
extraction:
  quarantine_below: 0.7        # confidence < 0.7 → quarantine, not stored
  judge_pass_below: 0.7        # triggers the expensive judge LLM call

freshness_days:
  pricing: 7
  feature: 14
  funding: 90
  hiring: 30
```

---

## Event Types

Every piece of information extracted from the web becomes a typed event:

| Type | What It Captures |
|---|---|
| `FeatureLaunchEvent` | New product features, capabilities, or integrations announced |
| `PricingChangeEvent` | Price increases, decreases, tier restructures, free plan changes |
| `FundingEvent` | Funding rounds, valuations, investor announcements |
| `AcquisitionEvent` | Acquisitions, mergers, acqui-hires |
| `PartnershipEvent` | Strategic alliances, technology partnerships, co-selling agreements |
| `HiringTrendEvent` | Bulk hiring in specific role categories (leading indicator) |
| `ProductUpdateEvent` | Changelog entries, bug fixes, performance improvements |
| `MarketTrendEvent` | Cross-competitor patterns (generated by Convergence Agent) |
| `CustomerSentimentEvent` | Aspect-level sentiment from G2/Capterra/Reddit reviews |

Every event carries: `company`, `timestamp`, `summary`, `source_urls`, `confidence_score`, `stakeholder_tags`, `schema_version`.

---

## Observability

Every LLM call is wrapped in a named Langfuse trace span. The span records:
- Agent name and operation
- Model used
- Input + output tokens
- Cost in USD
- Latency
- Success or failure

Cost is accumulated per pipeline run. Structured JSON logs include `agent`, `action`, `source`, `status`, `duration_ms`, `cost_usd`, `error_code` on every line.

### Named error codes

Every failure mode has a machine-readable code: `CRAWL_FAILED`, `CRAWL_BLOCKED`, `EXTRACTION_INVALID_SCHEMA`, `EXTRACTION_LOW_CONFIDENCE`, `SOURCE_TIMEOUT`, `CIRCUIT_OPEN`, `LLM_CALL_FAILED`. These are filterable, alertable, and distinguishable from each other.

---

## Quarantine System

Extractions with confidence < 0.7 go to quarantine rather than the knowledge base. The admin UI at `/admin` shows them side-by-side with the source text for review. Approved corrections feed the DSPy self-improving loop (Phase 5): 50+ corrections → weekly prompt optimization that improves future extraction accuracy automatically.

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/chat` | GET (SSE) | Streaming conversational query. Params: `message`, `session_id`, `stakeholder_role` |
| `/threats` | GET | Threat scores for all tracked competitors |
| `/matrix` | GET | Feature comparison matrix. Params: `companies[]` |
| `/narratives` | GET | Strategic narratives per competitor. Params: `company`, `days` |
| `/events` | GET | Recent events. Params: `company`, `event_type`, `days`, `limit` |
| `/health` | GET | Infrastructure health check (MongoDB, Supabase, Redis) |

---

## Running Tests

```bash
uv run pytest
```

Tests cover: schema validation, extraction agent (mocked LLM), narrative agent, threat scoring math, RSS crawler, and all error paths.

---

## Design Principles

This codebase follows strict production-grade conventions:

- **Single responsibility per agent** — no agent does two jobs
- **Schema first** — Pydantic models defined before any implementation
- **Prompts are not inline strings** — all prompts live in `prompts/`, versioned separately from agent logic
- **Structured output only** — Instructor + Pydantic on every LLM call, auto-retry on validation failure
- **Every LLM call is traced** — no LLM call runs without a Langfuse span
- **Named error codes** — every known failure mode has a machine-readable code
- **Events are immutable** — once written to MongoDB, never mutated; corrections create new version events
- **Configuration is data** — competitor list, model routing, thresholds are YAML files, not hardcoded values
- **Checkpointed state** — LangGraph pipeline resumes from the last checkpoint on failure, not from step 1

---

## License

MIT
