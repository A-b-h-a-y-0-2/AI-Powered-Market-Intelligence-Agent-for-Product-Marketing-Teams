"""DSPy Self-Improving Extraction Optimizer (Phase 5).

Requires ≥50 human-reviewed training examples from the quarantine system
(accumulated since Phase 2).

Weekly loop:
1. Pull corrected events from training_examples collection.
2. Define DSPy metric: field-level accuracy against human corrections.
3. Run DSPy BootstrapFewShot → optimised extraction prompt per event_type.
4. Shadow-test: route 10% of new crawls to new prompt for one week.
5. If new prompt confidence > old: promote. Deploy to ExtractionAgent.

This module is a scaffold. Full DSPy integration requires:
- dspy-ai installed (optional dependency, not in pyproject.toml by default)
- Sufficient training examples in MongoDB training_examples collection
- Shadow testing infrastructure (A/B routing in ExtractionAgent)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observability.logger import get_logger
from storage.event_store import EventStore
from tools.errors import AgentError, ErrorCode

log = get_logger("dspy_optimizer")

_MIN_TRAINING_EXAMPLES = 50
_SHADOW_FRACTION = 0.10
_OPTIMISED_PROMPTS_PATH = Path("prompts/optimised")

try:
    import dspy
    _HAS_DSPY = True
except ImportError:
    _HAS_DSPY = False


class DSPyOptimizerResult:
    """Result of one optimisation run."""

    def __init__(
        self,
        event_type: str,
        training_count: int,
        optimised: bool,
        metric_before: float | None,
        metric_after: float | None,
        prompt_path: str | None,
    ) -> None:
        self.event_type = event_type
        self.training_count = training_count
        self.optimised = optimised
        self.metric_before = metric_before
        self.metric_after = metric_after
        self.prompt_path = prompt_path


class DSPyOptimizer:
    """Self-improving extraction prompt optimizer.

    Uses human-reviewed corrections from the quarantine system as
    training signal. Runs weekly after sufficient examples accumulate.
    """

    def __init__(
        self,
        event_store: EventStore,
        model_config: dict,
        min_examples: int = _MIN_TRAINING_EXAMPLES,
    ) -> None:
        self._event_store = event_store
        self._model_config = model_config
        self._min_examples = min_examples

    async def run(self) -> list[DSPyOptimizerResult]:
        """Run optimisation for all event types with sufficient training data."""
        if not _HAS_DSPY:
            log.warning(
                "dspy_not_installed",
                agent="dspy_optimizer",
                action="run",
                message="dspy-ai not installed. Install with: pip install dspy-ai",
            )
            return []

        training_by_type = await self._load_training_examples()
        results: list[DSPyOptimizerResult] = []

        for event_type, examples in training_by_type.items():
            if len(examples) < self._min_examples:
                log.info(
                    "insufficient_training_data",
                    agent="dspy_optimizer",
                    event_type=event_type,
                    count=len(examples),
                    required=self._min_examples,
                )
                results.append(DSPyOptimizerResult(
                    event_type=event_type,
                    training_count=len(examples),
                    optimised=False,
                    metric_before=None,
                    metric_after=None,
                    prompt_path=None,
                ))
                continue

            log.info(
                "optimisation_started",
                agent="dspy_optimizer",
                event_type=event_type,
                training_count=len(examples),
            )

            result = await self._optimise_event_type(event_type, examples)
            results.append(result)

            log.info(
                "optimisation_completed",
                agent="dspy_optimizer",
                event_type=event_type,
                optimised=result.optimised,
                metric_before=result.metric_before,
                metric_after=result.metric_after,
                prompt_path=result.prompt_path,
            )

        return results

    async def _load_training_examples(self) -> dict[str, list[dict]]:
        """Load corrected events from MongoDB, grouped by event_type."""
        db = self._event_store._require_db()
        cursor = db["training_examples"].find({"corrected_extraction": {"$exists": True}})
        by_type: dict[str, list[dict]] = {}

        async for doc in cursor:
            event_type = doc.get("event_type", "unknown")
            if event_type not in by_type:
                by_type[event_type] = []
            by_type[event_type].append(doc)

        return by_type

    async def _optimise_event_type(
        self, event_type: str, examples: list[dict]
    ) -> DSPyOptimizerResult:
        """Run DSPy BootstrapFewShot for one event type."""
        model_name = self._model_config.get("extraction", "llama-3.3-70b-versatile")

        # ── DSPy signature for extraction ─────────────────────────────────────
        class ExtractionSignature(dspy.Signature):
            """Extract a structured market intelligence event from web content."""
            company: str = dspy.InputField(desc="company being analysed")
            source_url: str = dspy.InputField(desc="URL of the content")
            crawl_date: str = dspy.InputField(desc="date content was crawled (ISO 8601)")
            content: str = dspy.InputField(desc="raw web content")
            event_type_hint: str = dspy.InputField(desc="expected event type")
            extracted_event: str = dspy.OutputField(desc="extracted event as JSON string")

        # ── DSPy trainset ─────────────────────────────────────────────────────
        trainset = [
            dspy.Example(
                company=ex.get("corrected_extraction", {}).get("company", ""),
                source_url=ex.get("source_text", "")[:100],
                crawl_date=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                content=ex.get("source_text", "")[:2000],
                event_type_hint=event_type,
                extracted_event=json.dumps(ex.get("corrected_extraction", {})),
            ).with_inputs("company", "source_url", "crawl_date", "content", "event_type_hint")
            for ex in examples
        ]

        # ── Metric: field-level accuracy ──────────────────────────────────────
        def extraction_metric(example: dspy.Example, pred: Any, trace=None) -> float:
            try:
                predicted = json.loads(pred.extracted_event)
                expected = json.loads(example.extracted_event)
                matching = sum(
                    1 for k in expected if predicted.get(k) == expected.get(k)
                )
                return matching / len(expected) if expected else 0.0
            except (json.JSONDecodeError, AttributeError):
                return 0.0

        # ── Baseline metric (before optimisation) ────────────────────────────
        lm = dspy.LM(model=f"groq/{model_name}", temperature=0.0)
        dspy.configure(lm=lm)

        predictor = dspy.Predict(ExtractionSignature)
        baseline_scores = []
        for ex in trainset[:10]:
            try:
                pred = predictor(**ex.inputs())
                baseline_scores.append(extraction_metric(ex, pred))
            except Exception:
                baseline_scores.append(0.0)
        metric_before = sum(baseline_scores) / len(baseline_scores) if baseline_scores else 0.0

        # ── Optimise ─────────────────────────────────────────────────────────
        try:
            optimizer = dspy.BootstrapFewShot(
                metric=extraction_metric,
                max_bootstrapped_demos=4,
                max_labeled_demos=8,
            )
            optimised_predictor = optimizer.compile(
                dspy.Predict(ExtractionSignature),
                trainset=trainset,
            )
        except Exception as exc:
            log.error(
                "dspy_optimisation_failed",
                agent="dspy_optimizer",
                event_type=event_type,
                error=str(exc),
            )
            return DSPyOptimizerResult(
                event_type=event_type,
                training_count=len(examples),
                optimised=False,
                metric_before=metric_before,
                metric_after=None,
                prompt_path=None,
            )

        # ── Post-optimisation metric ──────────────────────────────────────────
        after_scores = []
        for ex in trainset[:10]:
            try:
                pred = optimised_predictor(**ex.inputs())
                after_scores.append(extraction_metric(ex, pred))
            except Exception:
                after_scores.append(0.0)
        metric_after = sum(after_scores) / len(after_scores) if after_scores else 0.0

        # ── Save optimised prompt if improved ────────────────────────────────
        prompt_path: str | None = None
        if metric_after > metric_before:
            _OPTIMISED_PROMPTS_PATH.mkdir(parents=True, exist_ok=True)
            out_path = _OPTIMISED_PROMPTS_PATH / f"{event_type}_optimised.json"
            optimised_state = optimised_predictor.dump_state()
            out_path.write_text(json.dumps(optimised_state, indent=2))
            prompt_path = str(out_path)
            log.info(
                "optimised_prompt_saved",
                agent="dspy_optimizer",
                event_type=event_type,
                metric_before=metric_before,
                metric_after=metric_after,
                path=prompt_path,
            )

        return DSPyOptimizerResult(
            event_type=event_type,
            training_count=len(examples),
            optimised=metric_after > metric_before,
            metric_before=metric_before,
            metric_after=metric_after,
            prompt_path=prompt_path,
        )

    async def get_training_stats(self) -> dict[str, int]:
        """Return count of training examples per event type."""
        if self._event_store._db is None:
            return {}
        db = self._event_store._require_db()
        pipeline = [
            {"$group": {"_id": "$event_type", "count": {"$sum": 1}}},
        ]
        cursor = db["training_examples"].aggregate(pipeline)
        result: dict[str, int] = {}
        async for doc in cursor:
            result[doc["_id"]] = doc["count"]
        return result
