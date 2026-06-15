"""Prometheus metrics for the market intelligence pipeline.

Exposes metrics via /metrics endpoint (FastAPI middleware or standalone).
All metrics use consistent label schemas: agent, status, competitor, event_type.

Usage:
    from observability.metrics import metrics
    metrics.crawl_requests.labels(agent="research_agent", status="success").inc()
    metrics.extraction_cost.labels(agent="extraction_agent").observe(0.003)
"""

from __future__ import annotations

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False


class _NoOpMetric:
    """No-op metric for when prometheus_client is not installed."""

    def labels(self, **_: str) -> "_NoOpMetric":
        return self

    def inc(self, _: float = 1) -> None: ...
    def dec(self, _: float = 1) -> None: ...
    def set(self, _: float) -> None: ...
    def observe(self, _: float) -> None: ...


class _NoOpRegistry:
    def generate_latest(self) -> bytes:
        return b"# prometheus_client not installed\n"

    def content_type(self) -> str:
        return "text/plain"


class PipelineMetrics:
    """All Prometheus metrics for the pipeline, grouped by layer."""

    def __init__(self, registry: "CollectorRegistry | None" = None) -> None:
        if not _HAS_PROMETHEUS:
            self._noop = True
            return
        self._noop = False
        reg = registry  # None uses the default global registry

        # ── Crawl layer ───────────────────────────────────────────────────────
        self.crawl_requests = Counter(
            "crawl_requests_total",
            "Total crawl requests by status",
            ["agent", "status", "competitor"],
            registry=reg,
        )
        self.crawl_changed = Counter(
            "crawl_content_changed_total",
            "Content changes detected by crawl",
            ["competitor"],
            registry=reg,
        )
        self.crawl_circuit_open = Counter(
            "crawl_circuit_open_total",
            "Circuit breaker open events per source",
            ["competitor"],
            registry=reg,
        )
        self.crawl_duration = Histogram(
            "crawl_duration_seconds",
            "Time spent crawling a single source",
            ["agent", "competitor"],
            buckets=[0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0],
            registry=reg,
        )

        # ── Extraction layer ──────────────────────────────────────────────────
        self.extraction_events = Counter(
            "extraction_events_total",
            "Events successfully extracted and stored",
            ["event_type", "competitor"],
            registry=reg,
        )
        self.extraction_quarantined = Counter(
            "extraction_quarantined_total",
            "Events sent to quarantine (low confidence)",
            ["event_type", "competitor"],
            registry=reg,
        )
        self.extraction_skipped = Counter(
            "extraction_skipped_total",
            "Documents skipped by pre-filter",
            ["competitor"],
            registry=reg,
        )
        self.extraction_cost = Histogram(
            "extraction_cost_usd",
            "LLM cost per extraction run in USD",
            ["agent"],
            buckets=[0.0001, 0.001, 0.005, 0.01, 0.05, 0.10, 0.50],
            registry=reg,
        )

        # ── LLM layer ─────────────────────────────────────────────────────────
        self.llm_calls = Counter(
            "llm_calls_total",
            "Total LLM API calls",
            ["agent", "model", "status"],
            registry=reg,
        )
        self.llm_tokens = Counter(
            "llm_tokens_total",
            "Total tokens consumed",
            ["agent", "model", "token_type"],
            registry=reg,
        )
        self.llm_cost = Counter(
            "llm_cost_usd_total",
            "Total LLM spend in USD",
            ["agent", "model"],
            registry=reg,
        )
        self.llm_latency = Histogram(
            "llm_latency_seconds",
            "LLM call latency",
            ["agent", "model"],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
            registry=reg,
        )

        # ── Pipeline layer ────────────────────────────────────────────────────
        self.pipeline_runs = Counter(
            "pipeline_runs_total",
            "Total pipeline runs by status",
            ["pipeline", "status"],
            registry=reg,
        )
        self.pipeline_duration = Histogram(
            "pipeline_duration_seconds",
            "Total pipeline run duration",
            ["pipeline"],
            buckets=[60, 300, 600, 1800, 3600],
            registry=reg,
        )
        self.pipeline_daily_cost = Gauge(
            "pipeline_daily_cost_usd",
            "Total pipeline cost for the current day",
            ["pipeline"],
            registry=reg,
        )

        # ── Quarantine layer ──────────────────────────────────────────────────
        self.quarantine_pending = Gauge(
            "quarantine_pending_count",
            "Number of events pending human review",
            registry=reg,
        )
        self.quarantine_correction_rate = Gauge(
            "quarantine_correction_rate",
            "Fraction of reviewed events that required correction",
            ["event_type"],
            registry=reg,
        )

        # ── API layer ─────────────────────────────────────────────────────────
        self.chat_requests = Counter(
            "chat_requests_total",
            "Total chat requests",
            ["status"],
            registry=reg,
        )
        self.chat_latency = Histogram(
            "chat_latency_seconds",
            "End-to-end chat response latency",
            buckets=[0.5, 1.0, 3.0, 8.0, 15.0, 30.0],
            registry=reg,
        )
        self.kb_hits = Counter(
            "kb_hits_total",
            "Knowledge base hits vs. Tavily fallbacks",
            ["result_type"],  # "kb_hit" | "tavily_fallback" | "kb_miss"
            registry=reg,
        )

    def _get(self, name: str):
        if self._noop:
            return _NoOpMetric()
        return getattr(self, name)

    def generate_latest(self) -> bytes:
        if not _HAS_PROMETHEUS:
            return b"# prometheus_client not installed\n"
        return generate_latest()

    @property
    def content_type(self) -> str:
        if not _HAS_PROMETHEUS:
            return "text/plain"
        return CONTENT_TYPE_LATEST


# Singleton — imported everywhere
metrics = PipelineMetrics()
