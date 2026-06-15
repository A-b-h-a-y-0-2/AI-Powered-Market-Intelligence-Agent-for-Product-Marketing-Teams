# Technology Choices — Market Intelligence Agent

---

## Guiding Principle

Every technology choice here is made against the architecture's three distinct responsibilities:

1. **Collecting** — scraping, crawling, monitoring external sources
2. **Transforming** — normalizing raw content into structured events
3. **Serving** — answering stakeholder queries through a conversational interface

A technology that is great at (1) may be completely wrong for (3). Choices are kept separate per responsibility.

---

## Layer 1: Data Collection

### Web Scraping & Crawling

| Tool | Role |
|---|---|
| **Firecrawl** | Primary crawler — handles JS-rendered pages, returns clean markdown, has sitemap crawling built-in |
| **Apify** | Specialized actors for LinkedIn company pages, G2/Capterra reviews, App Store listings — sources that general crawlers fail on |
| **Playwright** | Fallback for interactive pages (pricing pages behind login walls, changelogs that lazy-load) |

> **Why not just Apify for everything?** Apify actor availability varies. Firecrawl gives you direct URL-level control and markdown output that feeds cleanly into extraction prompts. Use both.

### Scheduled Triggering

| Tool | Role |
|---|---|
| **n8n** | Workflow scheduler for periodic crawl triggers — integrates with your existing stack |
| **APScheduler** (Python) | In-process scheduling for lightweight, high-frequency polling (e.g. RSS feeds every 30 mins) |

### Supplementary Search

| Tool | Role |
|---|---|
| **Tavily API** | LLM-optimized web search for discovery queries ("Competitor A funding news") |
| **RSS / Atom feeds** | Zero-cost, real-time monitoring for company blogs and changelogs that publish feeds |

---

## Layer 2: Processing & Extraction

### LLM Providers

| Provider | Role |
|---|---|
| **Groq (Llama 3 / Mixtral)** | High-speed, low-cost extraction tasks — "classify this content as a feature launch or pricing change" |
| **Claude 3.5 Sonnet** | Complex reasoning — multi-source synthesis, stakeholder insight generation, ambiguous classification |
| **Claude 3 Haiku** | Cheap fast pass — pre-filtering crawled content before expensive extraction |

> **Why two providers?** Extraction is a high-volume, low-complexity task. Routing it to Groq saves significant cost. Reserve Sonnet for actual intelligence generation.

### Structured Output Extraction

| Tool | Role |
|---|---|
| **Instructor** (Python library) | Forces LLM output to match Pydantic models — the correct way to guarantee valid `EventSchema` objects from raw text |
| **Pydantic v2** | Schema definitions for all event types — single source of truth for what a `FeatureLaunchEvent` looks like |

### Agent Orchestration

| Tool | Role |
|---|---|
| **LangGraph** | Core orchestration — models the agent workflow as a stateful graph with conditional edges (retry on crawl failure, escalate on low-confidence extraction, etc.) |
| **LangGraph Checkpointing** | Persistent agent state across scheduled runs — agent remembers what it already crawled |

---

## Layer 3: Storage

### Event Store

| Tool | Role |
|---|---|
| **MongoDB** | Primary event store — flexible document schema, good for heterogeneous event types, fast time-range queries |
| **MongoDB Atlas Search** | Full-text search over event summaries for conversational queries |

### Vector Store (Semantic Retrieval)

| Tool | Role |
|---|---|
| **Supabase (pgvector)** | Stores embeddings of event summaries — semantic search for conversational queries ("what competitor is moving into enterprise?") |
| **OpenAI `text-embedding-3-small`** | Embedding model — cheap, good quality, 1536 dimensions |

### Cache

| Tool | Role |
|---|---|
| **Redis** | Crawl result caching (hash of URL + timestamp → content) — prevents re-scraping unchanged pages |

---

## Layer 4: Conversational Interface

| Tool | Role |
|---|---|
| **FastAPI** | Agent API server — exposes `/chat`, `/events`, `/crawl-status` endpoints |
| **Next.js** | Frontend conversational UI — streaming responses, source citation display, stakeholder profile switcher |
| **LangGraph ReAct Agent** | Handles the conversational turn — decides whether to hit vector store, fetch recent events, or trigger a fresh crawl |

---

## Layer 5: Observability

| Tool | Role |
|---|---|
| **Langfuse** | Full LLM call tracing — captures prompt, output, latency, cost per agent step. Self-hostable. |
| **Prometheus + Grafana** | Operational metrics — crawl success rate, extraction failure rate, event ingestion throughput |
| **Python `logging` + structured JSON logs** | Per-run audit trail that feeds into both Grafana and Langfuse |

---

## What Is Deliberately Not Used

| Tool | Why excluded |
|---|---|
| **LlamaIndex** | LangGraph gives finer control over agent state; LlamaIndex adds abstraction where you need transparency |
| **Pinecone** | Supabase pgvector is sufficient at the scale of this problem and avoids an extra service |
| **LangChain chains** | LangGraph supersedes this; chains are too rigid for the conditional retry and multi-path logic required here |
| **AutoGPT / CrewAI** | Too opaque for a production system that requires observability and cost control |

---

## Summary Map

```
Collection     →  Firecrawl / Apify / Playwright / Tavily / RSS
Scheduling     →  n8n + APScheduler
Extraction     →  Instructor + Pydantic + Groq
Reasoning      →  Claude 3.5 Sonnet
Orchestration  →  LangGraph
Event Store    →  MongoDB
Vector Store   →  Supabase pgvector
Cache          →  Redis
API            →  FastAPI
UI             →  Next.js
Observability  →  Langfuse + Prometheus
```
