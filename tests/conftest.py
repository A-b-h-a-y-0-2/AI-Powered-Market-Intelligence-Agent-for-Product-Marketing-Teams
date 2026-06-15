"""Shared fixtures for all test modules.

All external dependencies (MongoDB, Redis, Supabase, LLM APIs) are replaced
with mocks or async stubs. No real network calls in any test.
"""

from __future__ import annotations

import pytest

# Configure structlog once at import time so get_logger() calls in module
# bodies (e.g. `log = get_logger("agent")`) use a working configuration.
from observability.logger import configure_logging
from observability.tracing import init_tracing

configure_logging("WARNING")
init_tracing(public_key="test", secret_key="test", enabled=False)


@pytest.fixture()
def fake_model_config() -> dict:
    return {
        "pre_filter": "claude-haiku-4-5-20251001",
        "extraction": "llama-3.3-70b-versatile",
        "validation": "claude-sonnet-4-6",
        "synthesis": "claude-sonnet-4-6",
        "conversational": "claude-sonnet-4-6",
    }


@pytest.fixture()
def fake_cost_config() -> dict:
    return {
        "claude-haiku-4-5-20251001": {"input": 0.00025, "output": 0.00125},
        "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
        "llama-3.3-70b-versatile": {"input": 0.0001, "output": 0.0001},
    }
