# Demo Script — AI-Powered Market Intelligence Agent

**Total runtime: ~5 minutes**
**Setup: API server running (`python main.py`) + frontend running (`npm run dev`) + pipeline has been run at least once (`python scripts/live_pipeline.py`)**

---

## Opening (30 seconds)

> "This is a production-grade AI system that turns the internet into structured competitive intelligence — automatically, every night, without anyone having to read a single news article.
>
> The core insight: research is offline, answers are online. A scheduled pipeline runs at 2am, crawls competitor websites, press releases, job boards, and review platforms, and converts everything it finds into typed, deduplicated events stored in a knowledge base. When you ask a question, the answer comes from that knowledge base — with source citations and a confidence score on every response.
>
> Let me show you the seven things this does."

**→ Open the browser to `http://localhost:3000`**

---

## 1. Home Page (20 seconds)

> "The home page shows you the system is alive — events ingested today, total events across all competitors, when the last research run happened. Seven consulting firms are tracked: McKinsey, BCG, Bain, Deloitte, KPMG, Oliver Wyman, Accenture Strategy.
>
> Here's the full data flow — raw web content comes in, goes through a 3-pass extraction pipeline, gets stored in MongoDB and pgvector, synthesised weekly into narratives and threat scores, and served on-demand through the conversational agent."

---

## 2. Threat Dashboard (45 seconds)

**→ Click "Threat Scoring" or navigate to `/dashboard`**

> "This is the first thing a managing partner would look at Monday morning. Each competitor is scored 0–100 on three components:
>
> — **Velocity**: how many events this week versus the 90-day average
> — **Type weighting**: pricing changes score 3x, feature launches 2x, blog posts 1x
> — **Recency decay**: events from yesterday matter more than events from last month
>
> The tier — HIGH, MEDIUM, or LOW — and the trend direction tells you at a glance who is accelerating and who is coasting.
>
> Click any competitor to see the events that drove its score."

---

## 3. Event Timeline (45 seconds)

**→ Click "Events" in the nav**

> "This is the knowledge base made visible. Every piece of information the system has extracted — feature launches, pricing changes, acquisitions, funding, partnerships, hiring trends — is stored as a typed event with a timestamp, a confidence score, and the source URL.
>
> I'll filter to McKinsey, last 30 days. Each card shows what happened, what type of event it is, the date, and a confidence bar — the Extraction Agent's certainty that this extraction is accurate.
>
> Click the source link on any event — it takes you to the exact article or press release the fact came from."

**→ Switch between companies to show the breadth. Try filtering by event type.**

---

## 4. Intelligence Chat — Factual Query (60 seconds)

**→ Click "Chat" in the nav. Make sure the Role is set to "Marketing".**

> "Now the most important part — the Conversational Agent. It's a 7-node graph: scope detection, company resolution, query classification, knowledge coverage evaluation, retrieval, response generation, and an attribution pass that matches every factual claim back to a source event."

**→ Type:** `What has McKinsey announced in AI partnerships in the last 60 days?`

> "Watch the status bar — you can see it routing: classifying the query, retrieving from the knowledge base, generating the answer, attributing sources. The answer arrives in under 10 seconds.
>
> Notice the confidence score below the response — that's the overall quality signal: source confidence combined with data freshness and coverage sufficiency. And the source chips — click any of them, they go directly to the source."

---

## 5. Intelligence Chat — Causal Chain (45 seconds)

**→ Still in Chat.**

> "Now let me ask something harder — a causal question."

**→ Type:** `Why is BCG investing heavily in AI right now?`

> "This is a causal chain query. The agent retrieves all BCG events from the last 180 days, sorts them chronologically, and constructs a causal hypothesis — not just a list of facts, but a reasoned story about *why* things are happening, with every step in the chain sourced to a real event.
>
> This is the thing that takes a junior analyst 2 hours to compile. It comes back in 8 seconds."

---

## 6. Intelligence Chat — Prediction (30 seconds)

**→ Still in Chat. Switch Role to "Sales".**

> "One more — a prediction query. The system uses hiring patterns as leading indicators."

**→ Type:** `What will Bain do in the next 6 months?`

> "The Hiring Signal Agent batches Indeed and Glassdoor postings weekly, flags anomalous role categories versus the 180-day baseline, and the LLM infers strategic direction from the pattern. Role categories hired now are product moves 4–9 months from now. The response is framed for a Sales rep — action-oriented, deal-focused."

---

## 7. Strategic Narratives (30 seconds)

**→ Click "Narratives" in the nav.**

> "The Narrative Agent runs Sunday at 5am. It takes all events for a competitor from the last 90 days, clusters them using DBSCAN, and for each cluster with 3+ events, synthesises a strategic story.
>
> Here's McKinsey's. 'AI-First Client Delivery Push' — five events over 60 days. The agent identified the underlying strategic intent: not just reporting what happened, but naming the pattern. Each key signal chip links back to the constituent events."

---

## 8. Feature Matrix (30 seconds)

**→ Click "Matrix" in the nav. Toggle to "Compare all".**

> "The Matrix Agent fires within 15 minutes of any feature launch or product update event. It classifies the feature into a predefined taxonomy — AI automation, CRM integration, analytics, security, pricing, API developer tools, content generation, workflow UX — and updates this living comparison table.
>
> Every cell with a checkmark traces back to a source event. This is the competitive landscape as a structured, sourced, living document — not a manually maintained spreadsheet."

---

## 9. Quarantine Review (20 seconds)

**→ Click "Quarantine" in the nav.**

> "The system is self-aware about what it doesn't know. The Extraction Agent uses a 3-pass pipeline — cheap pre-filter, structured extraction with Groq plus Instructor plus Pydantic, and a conditional judge pass. Anything with confidence below 0.70 goes to quarantine instead of the knowledge base.
>
> This is the human-in-the-loop. Corrections approved here feed Phase 5 — a DSPy self-improving loop that runs weekly and optimises extraction prompts per event type. The system gets more accurate the more it runs."

---

## 10. Pipeline Status (20 seconds)

**→ Click "Pipeline" in the nav.**

> "Finally — the engine room. Every agent has a schedule and a last-run timestamp. Research at 2am, extraction at 4am, sentiment at 5am. Sunday synthesis runs sequentially — hiring signals, narratives, convergence, threat scoring, then digests.
>
> The system is fully autonomous. Once it's running, a product marketer opens their laptop Monday morning and the intelligence is already there."

---

## Closing (20 seconds)

> "To summarise what you just saw:
>
> — Structured events extracted from 7 competitors, automatically, every night
> — Causal chains, predictions, and factual answers grounded in evidence
> — Strategic narratives detected from clusters of events
> — A living feature matrix updated in real time
> — Human review that makes the system smarter over time
>
> Research is offline. Answers are online. The whole pipeline runs on about $0.07–0.10 a day in LLM costs.
>
> Questions?"

---

## Suggested Follow-Up Questions (if time allows)

| Question | What it demonstrates |
|---|---|
| `How confident are you about Deloitte's AI strategy?` | Confidence score assembly, KB coverage evaluation |
| `What do McKinsey's clients actually think of their pricing?` | Sentiment Agent, ABSA per aspect |
| `Show me the market-wide trend in AI consulting` | Convergence Agent, cross-competitor analysis |
| `What are the weakest signals about KPMG this quarter?` | Low-confidence event retrieval, epistemic honesty |
| `Compare BCG and Bain on enterprise AI capabilities` | Comparison query type, multi-company retrieval |

---

## If the Backend Is Not Running

> "The API isn't connected right now, so you're seeing the UI in its empty state. Let me walk you through what each page shows when the pipeline has run…"

Then walk through the same script describing what each page *would* show. The empty states all have copy explaining what populates them.

---

## Startup Checklist

Before the demo, run these in order:

```bash
# Terminal 1 — API server
uv run python main.py

# Terminal 2 — Populate the KB (5–10 min, run once)
uv run python scripts/live_pipeline.py

# Terminal 3 — Frontend
cd frontend && npm run dev
```

Verify at `http://localhost:3000` — the "Live" badge in the header should be green.
