"""Named error codes for every known failure mode in the system.

Every error raised by agents or tools must carry one of these codes.
Named codes make failures filterable, alertable, and analyzable.
Generic exceptions are not acceptable for known failure modes.
"""

from __future__ import annotations


class ErrorCode:
    # ── Crawl layer ───────────────────────────────────────────────────────────
    CRAWL_FAILED = "CRAWL_FAILED"
    CRAWL_BLOCKED = "CRAWL_BLOCKED"
    CRAWL_TIMEOUT = "CRAWL_TIMEOUT"
    CRAWL_CONTENT_UNCHANGED = "CRAWL_CONTENT_UNCHANGED"
    CRAWL_INVALID_URL = "CRAWL_INVALID_URL"
    CRAWL_PARSE_ERROR = "CRAWL_PARSE_ERROR"

    # ── Search layer ──────────────────────────────────────────────────────────
    SEARCH_FAILED = "SEARCH_FAILED"
    SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
    SEARCH_RATE_LIMITED = "SEARCH_RATE_LIMITED"
    SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"

    # ── Extraction layer ──────────────────────────────────────────────────────
    EXTRACTION_INVALID_SCHEMA = "EXTRACTION_INVALID_SCHEMA"
    EXTRACTION_LOW_CONFIDENCE = "EXTRACTION_LOW_CONFIDENCE"
    EXTRACTION_PREFILTER_IRRELEVANT = "EXTRACTION_PREFILTER_IRRELEVANT"
    EXTRACTION_TEMPORAL_ANCHOR_FAILED = "EXTRACTION_TEMPORAL_ANCHOR_FAILED"

    # ── Deduplication ─────────────────────────────────────────────────────────
    DEDUP_MERGE_FAILED = "DEDUP_MERGE_FAILED"
    DEDUP_EMBED_FAILED = "DEDUP_EMBED_FAILED"

    # ── Storage layer ─────────────────────────────────────────────────────────
    STORE_WRITE_FAILED = "STORE_WRITE_FAILED"
    STORE_READ_FAILED = "STORE_READ_FAILED"
    STORE_CONNECTION_FAILED = "STORE_CONNECTION_FAILED"

    # ── Cache layer ───────────────────────────────────────────────────────────
    CACHE_READ_FAILED = "CACHE_READ_FAILED"
    CACHE_WRITE_FAILED = "CACHE_WRITE_FAILED"
    CACHE_CONNECTION_FAILED = "CACHE_CONNECTION_FAILED"

    # ── Vector store ──────────────────────────────────────────────────────────
    VECTOR_STORE_WRITE_FAILED = "VECTOR_STORE_WRITE_FAILED"
    VECTOR_STORE_READ_FAILED = "VECTOR_STORE_READ_FAILED"
    EMBED_FAILED = "EMBED_FAILED"

    # ── Circuit breaker ───────────────────────────────────────────────────────
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    CIRCUIT_HALF_OPEN = "CIRCUIT_HALF_OPEN"

    # ── Source / network ──────────────────────────────────────────────────────
    SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
    SOURCE_RATE_LIMITED = "SOURCE_RATE_LIMITED"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_AUTH_FAILED = "SOURCE_AUTH_FAILED"

    # ── Configuration ─────────────────────────────────────────────────────────
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISSING_FIELD = "CONFIG_MISSING_FIELD"

    # ── LLM / provider ───────────────────────────────────────────────────────
    LLM_CALL_FAILED = "LLM_CALL_FAILED"
    LLM_CONTEXT_TOO_LONG = "LLM_CONTEXT_TOO_LONG"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"

    # ── Agent ─────────────────────────────────────────────────────────────────
    AGENT_STATE_INVALID = "AGENT_STATE_INVALID"
    AGENT_CHECKPOINT_FAILED = "AGENT_CHECKPOINT_FAILED"

    # ── Conversational ────────────────────────────────────────────────────────
    QUERY_OUT_OF_SCOPE = "QUERY_OUT_OF_SCOPE"
    COMPANY_NOT_TRACKED = "COMPANY_NOT_TRACKED"
    COVERAGE_INSUFFICIENT = "COVERAGE_INSUFFICIENT"
    ATTRIBUTION_FAILED = "ATTRIBUTION_FAILED"


class AgentError(Exception):
    """Base exception for all agent and tool errors.

    Always carries a machine-readable error_code, the context of what failed,
    and optional upstream cause. Never raise a bare exception for known failure modes.
    """

    def __init__(
        self,
        code: str,
        message: str,
        context: dict | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.context = context or {}
        self.cause = cause
        super().__init__(f"[{code}] {message}")

    def to_dict(self) -> dict:
        return {
            "error_code": self.code,
            "error_message": self.message,
            "context": self.context,
        }


class CrawlError(AgentError):
    def __init__(
        self,
        code: str,
        message: str,
        source_url: str | None = None,
        context: dict | None = None,
        cause: Exception | None = None,
    ) -> None:
        ctx = dict(context or {})
        if source_url:
            ctx["source_url"] = source_url
        super().__init__(code=code, message=message, context=ctx, cause=cause)


class ExtractionError(AgentError):
    pass


class StorageError(AgentError):
    pass


class CacheError(AgentError):
    pass


class CircuitOpenError(AgentError):
    def __init__(self, source: str) -> None:
        super().__init__(
            code=ErrorCode.CIRCUIT_OPEN,
            message=f"Circuit breaker is open for source: {source}",
            context={"source": source},
        )


class ConfigError(AgentError):
    pass


class LLMError(AgentError):
    pass


class QueryError(AgentError):
    pass


class SearchError(AgentError):
    pass
