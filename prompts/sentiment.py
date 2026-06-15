"""ABSA (Aspect-Based Sentiment Analysis) prompts for the Sentiment Agent.

Predefined aspect taxonomy for v1 — consistency over completeness.
Aspects are defined in config; prompts import the aspect list dynamically.
"""

from __future__ import annotations

ABSA_SYSTEM = """You are an aspect-based sentiment analyst specialising in B2B SaaS product reviews.
Your job is to analyse customer reviews and extract sentiment for specific product aspects.

Rules:
1. Only analyse the aspects provided — do not invent new aspects.
2. A single review may produce multiple aspect results (one per relevant aspect mentioned).
3. Only produce a result for an aspect if the review explicitly mentions it.
   Do not infer sentiment for aspects that are not mentioned.
4. sentiment_score: -1.0 = strongly negative, 0.0 = neutral/mixed, +1.0 = strongly positive.
5. representative_quote: extract the exact sentence(s) from the review that support your classification.
   Do not paraphrase.
6. confidence_score: how confident are you that this aspect/sentiment assignment is correct?
   0.9+ = crystal clear, 0.7–0.89 = reasonably clear, below 0.7 = flag for review.

Return a JSON array of aspect sentiment objects. Empty array if no relevant aspects found."""


def build_absa_user_prompt(
    company: str,
    source_platform: str,
    review_text: str,
    aspects: list[str],
    aspect_descriptions: dict[str, str],
    crawl_date: str,
) -> str:
    aspect_list = "\n".join(
        f"- {a}: {aspect_descriptions.get(a, '')}" for a in aspects
    )
    return f"""Analyse this product review for aspect-level sentiment.

Company being reviewed: {company}
Platform: {source_platform}
Review date context: {crawl_date}

Aspects to analyse:
{aspect_list}

Review text:
---
{review_text[:3000]}
---

Return a JSON array. Each element:
{{
  "aspect": "one of the aspect keys above",
  "sentiment": "positive | negative | mixed",
  "sentiment_score": -1.0 to 1.0,
  "representative_quote": "exact sentence from review",
  "confidence_score": 0.0 to 1.0
}}

Return [] if no relevant aspects are mentioned in this review."""


ABSA_BATCH_SYSTEM = """You are an aspect-based sentiment aggregator for B2B SaaS product reviews.
Given multiple individual aspect sentiment results, aggregate them into summary statistics.

Return a JSON object with aggregated results per aspect."""


def build_absa_aggregate_prompt(
    company: str,
    aspect: str,
    results: list[dict],
    date_range: str,
) -> str:
    quotes = "\n".join(
        f"- (score: {r.get('sentiment_score', 0):.1f}) {r.get('representative_quote', '')}"
        for r in results[:10]
    )
    scores = [r.get("sentiment_score", 0) for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    return f"""Summarise aspect sentiment for {company} on the '{aspect}' aspect.

Period: {date_range}
Review count: {len(results)}
Average score: {avg_score:.2f}
Score distribution: {[round(s, 1) for s in scores[:20]]}

Sample quotes:
{quotes}

Return:
{{
  "sentiment": "positive | negative | mixed",
  "sentiment_score": weighted average as float,
  "summary": "2 sentence summary of what customers say about this aspect",
  "top_themes": ["theme 1", "theme 2", "theme 3"],
  "confidence_score": 0.0 to 1.0
}}"""
