# SOTA Advancements — Market Intelligence Agent

---

## What Qualifies as "SOTA" Here

Only advancements that are:
1. **Published or released** — not speculative
2. **Directly applicable** to this problem — not just impressive in general
3. **Usable today** — either as a paper-backed design pattern or as a library/API you can call

Each entry explains what it is, why it belongs in this project, and what the concrete implementation looks like.

---

## 1. Structured Output Extraction via Function Calling (Instructor + Pydantic)

**What it is**

Rather than prompting an LLM to "return JSON", the Instructor library forces the model to populate a Pydantic model by binding schema validation to the function-calling interface of modern LLMs. Invalid outputs are automatically retried with the validation error as feedback.

**Why it belongs here**

Your entire knowledge normalization layer depends on reliably converting a blog post into a structured `FeatureLaunchEvent`. This is the difference between a reliable production system and a fragile one.

**Concrete implementation**

```python
from instructor import patch
from openai import OpenAI
from pydantic import BaseModel

client = patch(OpenAI())

class FeatureLaunchEvent(BaseModel):
    company: str
    feature_name: str
    summary: str
    pricing_tier_affected: str | None
    timestamp: str
    source_url: str

event = client.chat.completions.create(
    model="gpt-4o-mini",
    response_model=FeatureLaunchEvent,
    messages=[{"role": "user", "content": raw_blog_text}]
)
```

**Status**: Production-ready. Instructor is actively maintained. Use this over manual JSON parsing.

---

## 2. LangGraph Multi-Agent Hierarchical Patterns

**What it is**

LangGraph allows building multi-agent systems as stateful graphs where a supervisor agent routes tasks to specialized sub-agents. The state machine approach — with explicit nodes, edges, and conditional routing — gives you control and observability that framework-level abstractions (CrewAI, AutoGPT) do not.

**Why it belongs here**

This project has at least four distinct agent responsibilities:
- **Research Agent** — decides what to crawl and when
- **Extraction Agent** — normalizes crawled content into events
- **Intelligence Agent** — generates stakeholder insights from events
- **Conversational Agent** — handles user queries

These are not steps in a chain. They run at different frequencies, can fail independently, and need to be observable separately. LangGraph is the right model for this.

**Pattern to use**

`Supervisor → subgraph per agent type → shared state (checkpointed in Postgres/SQLite)`

Each sub-agent has its own prompt, its own tools, and its own error handling. The supervisor decides which agent runs next based on the current state.

**Status**: LangGraph 0.2+ has stable multi-agent support. Study the official `multi_agent` examples in the LangGraph repo.

---

## 3. Corrective RAG (CRAG)

**What it is**

A pattern where the RAG pipeline includes an explicit evaluation step: after retrieving context, a lightweight LLM evaluator grades the relevance of retrieved documents. If relevance is low, the system falls back to a web search before generating the answer.

**Paper**: "Corrective Retrieval Augmented Generation" — Yan et al., 2024 (I believe this is accurate, but verify the exact paper title and authors before citing it formally).

**Why it belongs here**

Your knowledge base is always incomplete. A user might ask about something that happened yesterday but hasn't been crawled yet. CRAG prevents the system from confidently hallucinating an answer when the knowledge base doesn't have the relevant event.

**Concrete flow**

```
User Query
    ↓
Vector Retrieval (from MongoDB/pgvector)
    ↓
Relevance Evaluator (cheap LLM: "Is this context relevant to the query?")
    ↓ (if score < threshold)
Tavily Web Search → inject results into context
    ↓
Response Generation
```

**Status**: Pattern is well-established. LangGraph has example implementations. Not complex to build.

---

## 4. GraphRAG — Entity-Relationship Aware Retrieval

**What it is**

Microsoft's GraphRAG (2024) builds a knowledge graph from extracted entities and relationships before doing retrieval. Instead of retrieving isolated documents, it retrieves subgraphs — connected clusters of related entities and events.

**Why it belongs here**

Consider the query: *"How is Competitor A's product strategy evolving?"*

A flat vector search returns isolated events. GraphRAG returns: Competitor A → launched feature X (June) → which relates to Partnership Y (May) → which was preceded by Funding Event Z (March). The graph reveals the *story*, not just the facts.

**Concrete application here**

You don't need to implement the full GraphRAG paper. The applicable part is:
- After extracting events, build a lightweight entity graph (company → event → source → related companies)
- Use the graph to provide richer context for multi-hop queries in the conversational agent
- Neo4j or a simple MongoDB graph pattern works at this scale

**Status**: The GraphRAG Python library from Microsoft is available. I recommend studying the paper architecture first and implementing a simplified version rather than using the library directly — it has significant cost implications at full scale.

---

## 5. Self-RAG / Adaptive RAG

**What it is**

Self-RAG trains a model to generate inline reflection tokens: `[Retrieve]`, `[Relevant]`, `[Irrelevant]`, `[Supported]`, `[Contradicted]`. The model learns when it needs to retrieve and how to evaluate retrieved content.

The simpler, production-usable version is **Adaptive RAG**: using a lightweight classifier or LLM call to decide, per query, whether retrieval is needed and which retrieval path to use.

**Why it belongs here**

Not every user query requires a vector search. *"What is our company's product description?"* is answered from configuration. *"What happened with Competitor A this week?"* requires filtered event retrieval. *"What should the CEO know right now?"* requires synthesis across multiple event types.

Routing these incorrectly wastes LLM calls and degrades response quality.

**Concrete implementation**

```
Query Classification (cheap LLM call)
  → "factual_recent"    → MongoDB filtered query (timestamp + entity)
  → "semantic_search"   → pgvector cosine search
  → "synthesis"         → multi-step: retrieve events → synthesize → format per persona
  → "live_search"       → Tavily API
```

**Status**: Adaptive RAG is a standard LangGraph tutorial pattern. Study the LangGraph `adaptive_rag` example.

---

## 6. LLM-as-Judge for Extraction Quality

**What it is**

Using a secondary LLM call to evaluate the output of a primary LLM call. In extraction contexts, the judge verifies: "Is this event correctly classified? Is the summary accurate to the source? Is the confidence high enough to store?"

**Why it belongs here**

Your knowledge base quality determines your insight quality. A misclassified event (a blog post classified as a "Pricing Change" when it was a "Feature Launch") pollutes downstream analysis. At scale, you cannot manually review every extraction.

**Concrete implementation**

After extraction, run a validation pass:
```python
judge_prompt = f"""
Source text: {raw_text}
Extracted event: {extracted_event.json()}

Evaluate:
1. Is the event_type correct? (confidence: high/medium/low)
2. Is the summary accurate and not hallucinated?
3. Flag any fields that seem incorrect.

Return JSON: {{"confidence": "high|medium|low", "issues": [...]}}
"""
```

Events with `confidence: low` are quarantined for human review, not stored.

**Status**: Standard practice in production LLM pipelines. Not a single paper — multiple alignment and evaluation papers support this. Anthropic's Claude works particularly well as a judge.

---

## 7. Semantic Deduplication via Embedding Clustering

**What it is**

Rather than exact-match deduplication (same URL = same content), semantic deduplication uses embedding cosine similarity to detect when two different source documents describe the same real-world event. Near-duplicate events are merged, with all source URLs preserved.

**Why it belongs here**

A competitor launch will be covered by their blog, 2-3 news sites, LinkedIn, and Twitter. Without semantic deduplication, your event store becomes a noisy list of 6 events when there should be 1 event with 6 sources.

**Concrete implementation**

```python
# After extraction, before storing:
new_event_embedding = embed(new_event.summary)
recent_events = fetch_events(company=new_event.company, days=7)
recent_embeddings = [embed(e.summary) for e in recent_events]

similarities = cosine_similarity(new_event_embedding, recent_embeddings)
if max(similarities) > 0.88:  # threshold, tune empirically
    best_match = recent_events[argmax(similarities)]
    best_match.sources.append(new_event.source)
    update_event(best_match)
else:
    store_new_event(new_event)
```

**Status**: Production-ready pattern. Threshold tuning is the main empirical work required.

---

## 8. Observability: Full LLM Tracing with Langfuse

**What it is**

Langfuse is an open-source LLM observability platform that captures: prompts, completions, latency, cost, token counts, and custom metadata per LLM call. It supports nested tracing (trace the full agent run, with sub-spans for each tool call and LLM call).

**Why it belongs here**

The problem statement explicitly requires tracking "crawl success rate, extraction quality, agent reasoning steps, tool execution logs." Without structured observability, debugging a misbehaving agent in production is nearly impossible.

**Concrete implementation**

```python
from langfuse.decorators import observe, langfuse_context

@observe()
def extract_event(raw_text: str, source_url: str):
    langfuse_context.update_current_observation(
        metadata={"source_url": source_url}
    )
    result = instructor_client.extract(...)
    langfuse_context.update_current_observation(
        output=result.dict(),
        metadata={"confidence": result.confidence}
    )
    return result
```

**Status**: Langfuse v2 is production-ready. Self-host on your VPS or use their cloud. Better than LangSmith for cost-conscious setups.

---

## Summary: What Each SOTA Advancement Solves

| Advancement | Problem It Solves |
|---|---|
| Instructor + Pydantic | Reliable structured extraction from raw text |
| LangGraph Hierarchical Agents | Orchestrating multiple independent agents |
| Corrective RAG (CRAG) | Preventing answers when knowledge base is incomplete |
| GraphRAG (simplified) | Multi-hop queries across related events and entities |
| Adaptive RAG | Routing queries to the right retrieval path |
| LLM-as-Judge | Ensuring extraction quality before storing |
| Semantic Deduplication | Merging same events from multiple sources |
| Langfuse Tracing | Full observability over the agent pipeline |
