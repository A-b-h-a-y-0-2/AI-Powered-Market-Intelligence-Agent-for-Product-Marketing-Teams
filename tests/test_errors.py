"""Tests for named error codes and error class hierarchy.

CLAUDE.md: every error has a machine-readable code.
Tests verify that error subclasses carry context and are filterable by code.
"""

from __future__ import annotations

import pytest

from tools.errors import (
    AgentError,
    CacheError,
    CircuitOpenError,
    ConfigError,
    CrawlError,
    ErrorCode,
    ExtractionError,
    LLMError,
    QueryError,
    SearchError,
    StorageError,
)


class TestErrorCodes:
    def test_all_crawl_codes_are_strings(self):
        assert ErrorCode.CRAWL_FAILED == "CRAWL_FAILED"
        assert ErrorCode.CRAWL_BLOCKED == "CRAWL_BLOCKED"
        assert ErrorCode.CRAWL_TIMEOUT == "CRAWL_TIMEOUT"
        assert ErrorCode.CRAWL_CONTENT_UNCHANGED == "CRAWL_CONTENT_UNCHANGED"
        assert ErrorCode.CRAWL_PARSE_ERROR == "CRAWL_PARSE_ERROR"

    def test_all_extraction_codes_are_strings(self):
        assert ErrorCode.EXTRACTION_INVALID_SCHEMA == "EXTRACTION_INVALID_SCHEMA"
        assert ErrorCode.EXTRACTION_LOW_CONFIDENCE == "EXTRACTION_LOW_CONFIDENCE"
        assert ErrorCode.EXTRACTION_PREFILTER_IRRELEVANT == "EXTRACTION_PREFILTER_IRRELEVANT"

    def test_all_storage_codes_are_strings(self):
        assert ErrorCode.STORE_WRITE_FAILED == "STORE_WRITE_FAILED"
        assert ErrorCode.STORE_READ_FAILED == "STORE_READ_FAILED"
        assert ErrorCode.STORE_CONNECTION_FAILED == "STORE_CONNECTION_FAILED"

    def test_conversational_codes_exist(self):
        assert ErrorCode.QUERY_OUT_OF_SCOPE == "QUERY_OUT_OF_SCOPE"
        assert ErrorCode.COMPANY_NOT_TRACKED == "COMPANY_NOT_TRACKED"
        assert ErrorCode.COVERAGE_INSUFFICIENT == "COVERAGE_INSUFFICIENT"

    def test_search_codes_exist(self):
        assert ErrorCode.SEARCH_FAILED == "SEARCH_FAILED"
        assert ErrorCode.SEARCH_TIMEOUT == "SEARCH_TIMEOUT"
        assert ErrorCode.SEARCH_RATE_LIMITED == "SEARCH_RATE_LIMITED"


class TestAgentError:
    def test_carries_code_and_message(self):
        err = AgentError(
            code=ErrorCode.CRAWL_FAILED,
            message="Failed to fetch https://example.com",
        )
        assert err.code == "CRAWL_FAILED"
        assert "CRAWL_FAILED" in str(err)
        assert "Failed to fetch" in str(err)

    def test_to_dict_includes_error_code(self):
        err = AgentError(
            code=ErrorCode.EXTRACTION_INVALID_SCHEMA,
            message="Schema mismatch after 3 retries",
            context={"url": "https://example.com", "retries": 3},
        )
        d = err.to_dict()
        assert d["error_code"] == "EXTRACTION_INVALID_SCHEMA"
        assert d["context"]["retries"] == 3

    def test_default_context_is_empty_dict(self):
        err = AgentError(code=ErrorCode.LLM_CALL_FAILED, message="LLM failed")
        assert err.context == {}

    def test_cause_is_stored(self):
        original = ValueError("network error")
        err = CrawlError(
            code=ErrorCode.CRAWL_TIMEOUT,
            message="Timeout after 30s",
            cause=original,
        )
        assert err.cause is original

    def test_subclasses_are_filterable_by_type(self):
        errors = [
            CrawlError(code=ErrorCode.CRAWL_FAILED, message="crawl fail"),
            ExtractionError(code=ErrorCode.EXTRACTION_INVALID_SCHEMA, message="extraction fail"),
            StorageError(code=ErrorCode.STORE_WRITE_FAILED, message="storage fail"),
        ]
        crawl_errors = [e for e in errors if isinstance(e, CrawlError)]
        assert len(crawl_errors) == 1
        assert crawl_errors[0].code == "CRAWL_FAILED"


class TestCircuitOpenError:
    def test_carries_source(self):
        err = CircuitOpenError(source="https://example.com/blog")
        assert err.code == ErrorCode.CIRCUIT_OPEN
        assert "example.com" in err.message
        assert err.context["source"] == "https://example.com/blog"

    def test_is_agent_error_subclass(self):
        err = CircuitOpenError(source="https://example.com")
        assert isinstance(err, AgentError)


class TestErrorReraise:
    def test_error_chains_context_through_reraise(self):
        try:
            try:
                raise ConnectionError("refused")
            except ConnectionError as e:
                raise StorageError(
                    code=ErrorCode.STORE_CONNECTION_FAILED,
                    message="MongoDB unreachable at startup",
                    context={"host": "localhost", "port": 27017},
                    cause=e,
                ) from e
        except StorageError as exc:
            assert exc.code == "STORE_CONNECTION_FAILED"
            assert exc.context["host"] == "localhost"
            assert isinstance(exc.cause, ConnectionError)
            assert exc.__cause__ is exc.cause
