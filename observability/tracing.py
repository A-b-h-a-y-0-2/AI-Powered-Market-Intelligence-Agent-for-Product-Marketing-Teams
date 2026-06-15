"""Langfuse tracing integration.

Every LLM call is wrapped in a named trace span.
The span records: agent, operation, model, tokens, cost, latency, success/failure.
No LLM call runs without a trace.

Langfuse SDK >= 3.x API: uses start_as_current_observation() + update_current_generation().
The v2 client.trace() / trace.span() pattern is gone in v3+.

Usage:
    tracer = get_tracer()
    async with trace_span("extraction_agent", "extract_feature_launch") as span:
        result = llm_call(...)
        span.record_llm(
            model="llama-3.3-70b-versatile",
            input_tokens=512,
            output_tokens=128,
            cost_usd=0.0004,
        )
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from langfuse import Langfuse

_log = logging.getLogger("observability.tracing")


@dataclass
class SpanContext:
    """Holds a reference to the Langfuse client so agents can record LLM metadata.

    All methods are safe to call when _client is None (tracing disabled or setup failed).
    """

    _client: Any  # Langfuse instance or None
    _start_time: float = field(default_factory=time.monotonic)

    def record_llm(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        metadata: dict | None = None,
    ) -> None:
        """Record LLM call metadata on the active generation span."""
        if self._client is None:
            return
        try:
            self._client.update_current_generation(
                model=model,
                usage={
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                },
                metadata={
                    "cost_usd": cost_usd,
                    **(metadata or {}),
                },
            )
        except Exception as exc:
            _log.debug("Langfuse update_current_generation failed: %s", exc)

    def record_error(self, error_code: str, message: str) -> None:
        if self._client is None:
            return
        try:
            self._client.update_current_span(
                level="ERROR",
                status_message=f"[{error_code}] {message}",
            )
        except Exception as exc:
            _log.debug("Langfuse update_current_span failed: %s", exc)

    @property
    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._start_time) * 1000


class TracingConfig:
    def __init__(
        self,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        if enabled:
            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
        else:
            self._client = None

    @property
    def client(self) -> Langfuse | None:
        return self._client

    def flush(self) -> None:
        """Flush pending traces. Call at process shutdown."""
        if self._client:
            try:
                self._client.flush()
            except Exception as exc:
                _log.debug("Langfuse flush failed: %s", exc)


_tracer: TracingConfig | None = None


def init_tracing(
    public_key: str,
    secret_key: str,
    host: str = "https://cloud.langfuse.com",
    enabled: bool = True,
) -> TracingConfig:
    """Initialize the global tracer. Call once at application startup."""
    global _tracer
    _tracer = TracingConfig(public_key=public_key, secret_key=secret_key, host=host, enabled=enabled)
    return _tracer


def get_tracer() -> TracingConfig:
    if _tracer is None:
        raise RuntimeError(
            "Tracer not initialized. Call init_tracing() at application startup."
        )
    return _tracer


@asynccontextmanager
async def trace_span(
    agent_name: str,
    operation_name: str,
    run_id: str | None = None,
    metadata: dict | None = None,
) -> AsyncGenerator[SpanContext, None]:
    """Async context manager wrapping a unit of work in a Langfuse trace span.

    Uses the Langfuse v3+ API: start_as_current_observation() sets the active
    observation context; update_current_generation() / update_current_span()
    update it from anywhere in the call stack.

    If tracing is disabled or the Langfuse API call fails, yields a no-op
    SpanContext so agent code is unchanged.
    """
    tracer = get_tracer()
    client = tracer.client

    if client is None:
        yield SpanContext(_client=None)
        return

    # Enter the Langfuse observation context synchronously.
    # start_as_current_observation uses OTEL under the hood and is sync.
    _obs_cm = None
    try:
        _obs_cm = client.start_as_current_observation(
            name=f"{agent_name}.{operation_name}",
            type="GENERATION",
            input={"agent": agent_name, "run_id": run_id},
            metadata=metadata or {},
        )
        _obs_cm.__enter__()
    except Exception as exc:
        _log.debug("Langfuse start_as_current_observation failed: %s", exc)
        _obs_cm = None

    ctx = SpanContext(_client=client if _obs_cm else None)
    _raised: BaseException | None = None
    try:
        yield ctx
        if _obs_cm:
            try:
                client.update_current_span(
                    output={"status": "success", "elapsed_ms": ctx.elapsed_ms}
                )
            except Exception as exc:
                _log.debug("Langfuse span success update failed: %s", exc)
    except BaseException as exc:
        _raised = exc
        if _obs_cm:
            try:
                client.update_current_span(
                    level="ERROR",
                    status_message=str(exc),
                    output={"status": "error", "elapsed_ms": ctx.elapsed_ms},
                )
            except Exception as inner:
                _log.debug("Langfuse span error update failed: %s", inner)
        raise
    finally:
        if _obs_cm:
            try:
                if _raised:
                    _obs_cm.__exit__(type(_raised), _raised, _raised.__traceback__)
                else:
                    _obs_cm.__exit__(None, None, None)
            except Exception as exc:
                _log.debug("Langfuse observation exit failed: %s", exc)


def calculate_cost(model: str, input_tokens: int, output_tokens: int, costs: dict) -> float:
    """Calculate LLM call cost in USD from model costs config.

    The costs dict uses keys "input" and "output" (cost per 1k tokens each).
    """
    model_costs = costs.get(model)
    if not model_costs:
        return 0.0
    return (
        (input_tokens / 1000) * model_costs.get("input", 0.0)
        + (output_tokens / 1000) * model_costs.get("output", 0.0)
    )
