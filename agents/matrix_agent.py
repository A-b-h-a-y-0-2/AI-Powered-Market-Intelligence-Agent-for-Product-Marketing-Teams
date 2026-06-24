"""Matrix Agent — maintains the living feature comparison matrix.

Trigger: Fires ≤15 minutes after any FeatureLaunchEvent or ProductUpdateEvent (debounced).
Input: The triggering event + current feature matrix state for that company.
Output: Updated feature_matrix document in MongoDB.

The matrix is pre-computed at write time so queries get instant reads (~200ms).
No matrix is computed at query time.

Debounce: Redis key `matrix_debounce:{company}` with 15-minute TTL prevents
multiple events in quick succession from triggering redundant re-computations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import instructor
import yaml
from groq import Groq

from agents.base import BaseAgent
from observability.logger import get_logger
from observability.tracing import calculate_cost, trace_span
from storage.cache import CacheStore
from storage.event_store import EventStore
from tools.errors import ErrorCode

log = get_logger("matrix_agent")

_DEBOUNCE_KEY_PREFIX = "matrix_debounce:"
_DEBOUNCE_TTL_SECONDS = 900  # 15 minutes

_TAXONOMY_PATH = Path("config/feature_taxonomy.yaml")


def _load_taxonomy() -> dict[str, dict]:
    """Load feature taxonomy from config. Fails loudly if missing."""
    if not _TAXONOMY_PATH.exists():
        raise FileNotFoundError(f"Feature taxonomy not found: {_TAXONOMY_PATH}")
    with open(_TAXONOMY_PATH) as f:
        data = yaml.safe_load(f)
    return {
        cat: {"description": meta["description"], "keywords": meta["keywords"]}
        for cat, meta in data.get("categories", {}).items()
    }


class FeatureCategoryResult:
    """Output of the LLM taxonomy classifier."""

    def __init__(self, category: str, confidence: float, reasoning: str, concise_name: str = "") -> None:
        self.category = category
        self.confidence = confidence
        self.reasoning = reasoning
        self.concise_name = concise_name


class MatrixAgent(BaseAgent):
    """Classifies new features into the taxonomy and updates the feature matrix.

    Debounced: will not re-run within 15 minutes for the same company.
    Uses Groq (cheap, fast) for taxonomy classification — this is a short
    classification task, not synthesis.
    """

    name = "matrix_agent"
    description = (
        "Event-triggered agent that classifies new features/updates into the feature taxonomy "
        "and updates the pre-computed feature_matrix document in MongoDB. "
        "Debounced at 15 minutes per company. "
        "Trigger: FeatureLaunchEvent or ProductUpdateEvent."
    )

    def __init__(
        self,
        event_store: EventStore,
        cache: CacheStore,
        model_config: dict,
        cost_config: dict,
    ) -> None:
        self._event_store = event_store
        self._cache = cache
        self._model_config = model_config
        self._cost_config = cost_config
        self._groq_client = instructor.from_groq(Groq())
        self._taxonomy = _load_taxonomy()

    async def run(
        self,
        event: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Process a triggering event and update the feature matrix.

        Returns a dict with: company, action (updated|skipped), category, feature_name.
        """
        run_id = run_id or str(uuid.uuid4())
        company = event.get("company", "")
        event_id = str(event.get("_id", event.get("event_id", run_id)))

        if not company:
            log.error(
                "matrix_missing_company",
                agent=self.name,
                action="run",
                error_code=ErrorCode.AGENT_STATE_INVALID,
                event_id=event_id,
            )
            return {"action": "skipped", "reason": "missing_company"}

        # Debounce check
        debounce_key = f"{_DEBOUNCE_KEY_PREFIX}{company}"
        is_debounced = await self._cache.get(debounce_key)
        if is_debounced:
            log.info(
                "matrix_debounced",
                agent=self.name,
                action="run",
                competitor=company,
                status="skip",
            )
            return {"action": "skipped", "reason": "debounced", "company": company}

        # Set debounce lock
        await self._cache.set(debounce_key, "1", ttl_seconds=_DEBOUNCE_TTL_SECONDS)

        feature_name = event.get("feature_name") or event.get("summary", "")[:150]
        event_type = event.get("event_type", "")

        log.info(
            "matrix_update_started",
            agent=self.name,
            action="run",
            competitor=company,
            event_type=event_type,
            feature=feature_name,
            run_id=run_id,
        )

        # Classify feature into taxonomy
        category_result = await self._classify_feature(
            feature_name=feature_name,
            event_summary=event.get("summary", ""),
            event_type=event_type,
            company=company,
        )

        # Load existing matrix
        matrix_doc = await self._event_store.get_feature_matrix(company=company)
        if not matrix_doc:
            matrix_doc = {
                "company": company,
                "taxonomy_version": "1.0",
                "features": {cat: [] for cat in self._taxonomy},
            }

        # Append the new feature entry
        resolved_name = category_result.concise_name or feature_name
        new_entry = {
            "name": resolved_name,
            "description": event.get("summary", "")[:400],
            "launched_date": event.get("timestamp", datetime.now(tz=timezone.utc).isoformat())[:10],
            "source_event_id": event_id,
            "event_type": event_type,
            "classifier_confidence": str(round(category_result.confidence, 3)),
        }

        category = category_result.category
        if category not in matrix_doc["features"]:
            matrix_doc["features"][category] = []

        # Avoid duplicate entries for the same feature name
        existing_names = {f["name"] for f in matrix_doc["features"][category]}
        if resolved_name not in existing_names:
            matrix_doc["features"][category].append(new_entry)

        # Persist updated matrix
        await self._event_store.upsert_feature_matrix(company=company, matrix=matrix_doc)

        log.info(
            "matrix_updated",
            agent=self.name,
            action="run",
            competitor=company,
            category=category,
            feature=feature_name,
            confidence=category_result.confidence,
            status="ok",
        )

        return {
            "action": "updated",
            "company": company,
            "category": category,
            "feature_name": feature_name,
            "classifier_confidence": category_result.confidence,
        }

    async def _classify_feature(
        self,
        feature_name: str,
        event_summary: str,
        event_type: str,
        company: str,
    ) -> FeatureCategoryResult:
        """Classify a feature into the taxonomy using Groq."""
        model = self._model_config.get("extraction", "llama-3.3-70b-versatile")

        taxonomy_lines = "\n".join(
            f"- {cat}: {meta['description']} (keywords: {', '.join(meta['keywords'][:5])})"
            for cat, meta in self._taxonomy.items()
        )

        prompt = f"""Classify this product feature into one taxonomy category and extract a concise name.

Feature/event text: {feature_name}
Event summary: {event_summary}
Event type: {event_type}
Company: {company}

Taxonomy categories:
{taxonomy_lines}

Return JSON:
{{
  "category": "the category key that best fits (use exactly one of the keys above)",
  "confidence": 0.0 to 1.0,
  "reasoning": "one sentence",
  "concise_name": "3-7 word product or feature name only, no company name, no verbs (e.g. 'QuantumBlack AI Studio', 'Responsible AI Monitor')"
}}"""

        from pydantic import BaseModel

        class ClassificationResult(BaseModel):
            category: str
            confidence: float
            reasoning: str
            concise_name: str

        async with trace_span(self.name, "classify_feature") as span:
            try:
                result = self._groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You classify product features into a taxonomy. Return JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    response_model=ClassificationResult,
                    max_retries=2,
                )
                input_tokens = len(prompt) // 4
                output_tokens = 64
                cost = calculate_cost(model, input_tokens, output_tokens, self._cost_config)
                span.record_llm(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )

                # Validate category is in taxonomy
                if result.category not in self._taxonomy:
                    # Pick closest by keyword match
                    result.category = self._fallback_classify(feature_name)
                    result.confidence = 0.5

                return FeatureCategoryResult(
                    category=result.category,
                    confidence=result.confidence,
                    reasoning=result.reasoning,
                    concise_name=result.concise_name,
                )
            except Exception as exc:
                log.error(
                    "feature_classification_failed",
                    agent=self.name,
                    error_code=ErrorCode.LLM_CALL_FAILED,
                    error=str(exc),
                )
                return FeatureCategoryResult(
                    category=self._fallback_classify(feature_name),
                    confidence=0.3,
                    reasoning="classification_failed_fallback",
                )

    def _fallback_classify(self, feature_name: str) -> str:
        """Keyword-based fallback when LLM classifier fails."""
        name_lower = feature_name.lower()
        best_category = "workflow_ux"
        best_score = 0

        for cat, meta in self._taxonomy.items():
            score = sum(1 for kw in meta["keywords"] if kw in name_lower)
            if score > best_score:
                best_score = score
                best_category = cat

        return best_category

    async def health_check(self) -> dict:
        store_ok = await self._event_store.health_check()
        cache_ok = await self._cache.health_check()
        taxonomy_ok = bool(self._taxonomy)
        all_ok = store_ok and cache_ok and taxonomy_ok
        return {
            "agent": self.name,
            "status": "ok" if all_ok else "degraded",
            "dependencies": {
                "event_store": "ok" if store_ok else "failed",
                "cache": "ok" if cache_ok else "failed",
                "taxonomy": "ok" if taxonomy_ok else "failed",
            },
        }
