# Research Topics — Market Intelligence Agent

---

## What "Research Topic" Means Here

These are not academic literature reviews. Each topic below is a real, unresolved engineering and AI problem that this project will require you to solve. You should understand the current thinking, known failure modes, and make a deliberate design decision per topic before writing any code.

---

## Topic 1: Event Detection from Unstructured Web Content

**The Problem**

A blog post might say:
> "We're excited to introduce AI-powered lead scoring, available today on all paid plans."

You need to reliably extract:
- **Event type**: Feature Launch
- **Entity**: Competitor A
- **Attribute**: Lead Scoring (AI-powered)
- **Scope**: Paid plans only
- **Date**: Today (requires temporal anchoring to crawl timestamp)

Raw prompting fails here at scale — outputs are inconsistent. This is the core extraction problem.

**What to Study**
- Information Extraction (IE) as a classical NLP problem — named entity recognition, relation extraction, event detection
- The difference between **Open IE** (extract whatever is there) and **Schema-guided IE** (extract only what matches your event schema)
- How Instructor library implements structured extraction using function calling — understand this deeply, it is your primary tool

**Key Design Decision**
Schema-guided extraction with strict Pydantic validation beats open extraction every time for a production system. Define your event schemas first. Everything else follows.

---

## Topic 2: Change Detection in Web Content

**The Problem**

You're crawling a competitor's pricing page every 24 hours. How do you know something actually changed? Naively re-extracting everything is expensive. Storing full page diffs is noisy (nav changes, timestamps, ads change constantly).

**What to Study**
- Content-hash based deduplication — hash the meaningful content, not the full HTML
- Semantic change detection — did the *meaning* change, even if the wording changed?
- Targeted extraction vs. full-page extraction — only re-extract structured fields that matter (price tiers, feature names)
- Diff-based change signals — approximate content diffing before triggering LLM extraction

**Key Design Decision**
You need a two-pass strategy: cheap hash comparison first, semantic comparison second (embedding cosine distance), LLM extraction only when a real change is detected.

---

## Topic 3: Source Deduplication and Event Merging

**The Problem**

A competitor launches a feature. You see it on:
- Their blog (June 10)
- TechCrunch (June 11)
- A LinkedIn post by their CEO (June 11)
- Product Hunt listing (June 12)

These should merge into one event with multiple sources, not four separate events. Today's naive approaches either miss the merge (creating noise) or over-merge (losing signal).

**What to Study**
- Entity resolution as a research problem — when are two text snippets about the same real-world event?
- Embedding-based deduplication — compute cosine similarity between event summaries, cluster near-duplicates
- Canonical event representation — what makes a "same event" decision trustworthy vs. risky?
- LLM-as-merge-judge — using a cheap LLM to decide "is event A the same as event B?"

**Key Design Decision**
Deduplicate at the event level (after extraction), not at the raw content level. Two articles that say the same thing in different words should be one event with two sources.

---

## Topic 4: Temporal Reasoning in Knowledge Retrieval

**The Problem**

A user asks: *"What happened with Competitor A this week?"*

Your vector store returns the top-k semantically similar events, but some of them are from 3 months ago. Semantic similarity and recency are two completely different axes. A vector search alone does not understand time.

**What to Study**
- Hybrid retrieval — combining vector similarity with hard timestamp filters
- Recency decay functions — how to downweight older events in ranking
- Temporal entity tracking — "Competitor A raised prices" in Jan vs. "Competitor A raised prices" in May are separate events, not duplicates
- Time-aware embedding — research on whether embeddings should encode temporal context (answer: mostly no, use metadata filters instead)

**Key Design Decision**
Never rely on pure vector search for this use case. Always use filtered retrieval: `WHERE timestamp > X AND company = Y`, then rank by semantic relevance within that filtered set.

---

## Topic 5: Persona-Conditioned Response Generation

**The Problem**

The same event (competitor launches AI lead scoring) should produce fundamentally different outputs:
- **CEO**: "Market is moving toward automated qualification — our positioning needs revisiting"
- **Sales**: "Expect objection: 'your competitor has this built in'"
- **Product**: "Evaluate whether to build or partner for this capability"

This is not just a "tone" change. It requires the system to understand what each stakeholder cares about and what decisions they are trying to make.

**What to Study**
- Persona-conditioned generation — how to encode stakeholder intent as context
- Role-specific framing of the same factual content
- System prompt engineering for audience-adaptive summarization
- Whether to use separate prompts per persona or a unified prompt with persona injection

**Key Design Decision**
Maintain a stakeholder profile registry (what they care about, what decisions they make, what vocabulary they use). Inject the relevant profile at generation time. Do not try to generate "all personas" in one call.

---

## Topic 6: Retrieval Strategy for Conversational Agents

**The Problem**

When a user asks a question, what does the agent retrieve? The naive answer is "top-k vector search." But:
- Some questions need recent events (temporal)
- Some questions need company-specific events (entity filter)
- Some questions need cross-competitor synthesis (multi-entity retrieval)
- Some questions need a fresh web search (nothing in the knowledge base yet)

Getting this routing wrong produces hallucinated or stale answers.

**What to Study**
- Query decomposition — breaking a user question into sub-queries
- Retrieval routing — deciding which retrieval path to use per query type
- Self-RAG / Corrective RAG — the agent evaluates whether retrieved context is sufficient before answering
- When to trigger a live crawl vs. answer from stored knowledge

**Key Design Decision**
The conversational agent needs explicit routing logic, not just a single retrieval call. Define a retrieval decision tree: structured query → MongoDB; semantic query → pgvector; "what's happening now?" → trigger Tavily search.

---

## Topic 7: Source Attribution and Claim Grounding

**The Problem**

The product spec requires every insight to be traceable. This is harder than it sounds. An LLM summarizing five events may produce a sentence that paraphrases all five — but which sentence comes from which source? Standard generation does not track this.

**What to Study**
- Attributed generation — models that generate text with inline citation markers
- Post-hoc attribution — matching generated claims back to source documents after generation
- Quote extraction vs. paraphrase tracking — quoting is reliable; paraphrase attribution is research-level hard
- Citation hallucination — LLMs confidently cite sources that don't exist; how to prevent this

**Key Design Decision**
Do not ask the model to generate with inline citations — this leads to hallucinated citations. Instead, generate the insight first, then run a separate attribution pass that matches each claim to a specific event ID in your database.

---

## Summary Table

| Topic | Core Problem | Your Key Decision |
|---|---|---|
| Event Detection | Reliable structured extraction from prose | Schema-guided extraction with Instructor + Pydantic |
| Change Detection | Did the page actually change? | Two-pass: hash → semantic diff → LLM extraction |
| Source Deduplication | Same event from multiple sources | Merge at event level using embedding similarity |
| Temporal Reasoning | Recency vs. relevance in retrieval | Filtered retrieval: timestamp + entity first, then vector rank |
| Persona-Conditioned Generation | Different insight for different stakeholders | Stakeholder registry injected at generation time |
| Retrieval Strategy | Which retrieval path for which query type | Explicit routing logic, not single vector search |
| Source Attribution | Every claim must trace to a real source | Generate first, attribute second via a dedicated pass |
