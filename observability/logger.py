"""Structured JSON logger.

Every log line is a JSON object with consistent fields:
  timestamp, level, agent, action, source, status, duration_ms, cost_usd, error_code

Never use print() for anything that matters. All observability goes through this logger.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import structlog

_CONFIGURED = False


def _run_id_interceptor(_logger: Any, _method: str, event_dict: dict) -> dict:
    """Structlog processor: if the log event carries a run_id, push a JSON
    snapshot to the pipeline live-log buffer so SSE clients see it instantly."""
    run_id = event_dict.get("run_id")
    if run_id:
        try:
            from api.routes import push_pipeline_log
            push_pipeline_log(run_id, json.dumps(event_dict, default=str))
        except Exception:
            pass  # never let the interceptor crash the logger
    return event_dict


def _configure_structlog(log_level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _run_id_interceptor,
    ]

    if sys.stderr.isatty():
        # Human-readable in development
        renderer = structlog.dev.ConsoleRenderer()
    else:
        # JSON in production / CI
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(agent: str) -> structlog.BoundLogger:
    """Return a bound logger pre-populated with the agent name.

    Usage:
        log = get_logger("extraction_agent")
        log.info("event_extracted", source=url, event_type="feature_launch", cost_usd=0.003)
        log.error("crawl_failed", error_code="CRAWL_BLOCKED", source=url, retry_count=3)
    """
    _configure_structlog()
    return structlog.get_logger().bind(agent=agent)


def configure_logging(log_level: str = "INFO") -> None:
    """Call once at application startup to configure structlog."""
    _configure_structlog(log_level)
