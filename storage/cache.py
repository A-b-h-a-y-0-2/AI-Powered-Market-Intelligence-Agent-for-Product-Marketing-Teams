"""Redis cache store.

Responsibilities:
- Cache crawl results by URL + date bucket (content hash → prevent re-extraction)
- Store circuit breaker state per source (failure count, circuit state)
- Store conversational session history per session_id

All methods raise CacheError with named error codes — never return None on failure.
Every external call has an explicit timeout.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

from tools.errors import CacheError, ErrorCode


class CacheStore:
    """Async Redis client with typed methods and explicit error handling."""

    def __init__(self, redis_url: str, default_ttl_seconds: int = 86400) -> None:
        self._url = redis_url
        self._default_ttl = default_ttl_seconds
        self._client: Redis | None = None

    async def connect(self) -> None:
        try:
            self._client = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            await self._client.ping()
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_CONNECTION_FAILED,
                message=f"Failed to connect to Redis at {self._url}",
                cause=exc,
            ) from exc

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> Redis:
        if self._client is None:
            raise CacheError(
                code=ErrorCode.CACHE_CONNECTION_FAILED,
                message="CacheStore not connected. Call connect() first.",
            )
        return self._client

    # ── Crawl content cache ───────────────────────────────────────────────────

    def _crawl_key(self, url: str, date_bucket: str) -> str:
        """date_bucket is a YYYY-MM-DD string for 24-hour bucketing."""
        return f"crawl:{date_bucket}:{url}"

    async def get_content_hash(self, url: str, date_bucket: str) -> str | None:
        """Return cached content hash or None if not cached."""
        client = self._require_client()
        try:
            return await client.get(self._crawl_key(url, date_bucket))
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_READ_FAILED,
                message=f"Failed to read content hash for {url}",
                context={"url": url, "date_bucket": date_bucket},
                cause=exc,
            ) from exc

    async def set_content_hash(
        self, url: str, date_bucket: str, content_hash: str, ttl_seconds: int | None = None
    ) -> None:
        client = self._require_client()
        try:
            await client.set(
                self._crawl_key(url, date_bucket),
                content_hash,
                ex=ttl_seconds or self._default_ttl,
            )
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to write content hash for {url}",
                context={"url": url},
                cause=exc,
            ) from exc

    async def get_etag(self, url: str) -> str | None:
        client = self._require_client()
        try:
            return await client.get(f"etag:{url}")
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_READ_FAILED,
                message=f"Failed to read ETag for {url}",
                context={"url": url},
                cause=exc,
            ) from exc

    async def set_etag(self, url: str, etag: str, ttl_seconds: int | None = None) -> None:
        client = self._require_client()
        try:
            await client.set(f"etag:{url}", etag, ex=ttl_seconds or self._default_ttl)
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to write ETag for {url}",
                context={"url": url},
                cause=exc,
            ) from exc

    # ── Circuit breaker state ─────────────────────────────────────────────────

    def _circuit_key(self, source_url: str) -> str:
        return f"circuit:{source_url}"

    async def get_circuit_state(self, source_url: str) -> dict[str, Any]:
        """Return circuit breaker state for a source. Defaults to closed circuit."""
        client = self._require_client()
        try:
            raw = await client.get(self._circuit_key(source_url))
            if raw is None:
                return {"state": "CLOSED", "failure_count": 0, "opened_at": None}
            return json.loads(raw)
        except CacheError:
            raise
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_READ_FAILED,
                message=f"Failed to read circuit state for {source_url}",
                context={"source": source_url},
                cause=exc,
            ) from exc

    async def set_circuit_state(
        self, source_url: str, state: dict[str, Any], ttl_seconds: int = 7200
    ) -> None:
        client = self._require_client()
        try:
            await client.set(
                self._circuit_key(source_url),
                json.dumps(state),
                ex=ttl_seconds,
            )
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to write circuit state for {source_url}",
                context={"source": source_url},
                cause=exc,
            ) from exc

    async def increment_failure_count(self, source_url: str) -> int:
        """Increment failure counter for a source. Returns new count."""
        client = self._require_client()
        key = f"circuit_failures:{source_url}"
        try:
            count = await client.incr(key)
            await client.expire(key, 3600)
            return count
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to increment failure count for {source_url}",
                context={"source": source_url},
                cause=exc,
            ) from exc

    async def reset_failure_count(self, source_url: str) -> None:
        client = self._require_client()
        try:
            await client.delete(f"circuit_failures:{source_url}")
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to reset failure count for {source_url}",
                context={"source": source_url},
                cause=exc,
            ) from exc

    # ── Session store (conversational agent) ──────────────────────────────────

    async def get_session(self, session_id: str) -> list[dict[str, Any]]:
        client = self._require_client()
        try:
            raw = await client.get(f"session:{session_id}")
            if raw is None:
                return []
            return json.loads(raw)
        except CacheError:
            raise
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_READ_FAILED,
                message=f"Failed to read session {session_id}",
                cause=exc,
            ) from exc

    async def set_session(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        ttl_seconds: int = 7200,
    ) -> None:
        client = self._require_client()
        try:
            await client.set(
                f"session:{session_id}",
                json.dumps(history),
                ex=ttl_seconds,
            )
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to write session {session_id}",
                cause=exc,
            ) from exc

    # ── Generic key-value (used by MatrixAgent debounce, etc.) ───────────────────

    async def get(self, key: str) -> str | None:
        """Get an arbitrary key. Returns None if missing."""
        client = self._require_client()
        try:
            return await client.get(key)
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_READ_FAILED,
                message=f"Failed to read key {key}",
                cause=exc,
            ) from exc

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        """Set an arbitrary key with optional TTL."""
        client = self._require_client()
        try:
            await client.set(key, value, ex=ttl_seconds)
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to write key {key}",
                cause=exc,
            ) from exc

    async def delete(self, key: str) -> None:
        """Delete a key. No-op if the key does not exist."""
        client = self._require_client()
        try:
            await client.delete(key)
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_WRITE_FAILED,
                message=f"Failed to delete key {key}",
                cause=exc,
            ) from exc

    # ── Session history aliases (used by ConversationalAgent) ────────────────────

    async def get_session_history(self, session_id: str) -> list[dict[str, Any]] | None:
        """Alias for get_session, returns None instead of [] when not found."""
        client = self._require_client()
        try:
            raw = await client.get(f"session:{session_id}")
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            raise CacheError(
                code=ErrorCode.CACHE_READ_FAILED,
                message=f"Failed to read session history {session_id}",
                cause=exc,
            ) from exc

    async def save_session_history(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        ttl_seconds: int = 7200,
    ) -> None:
        """Alias for set_session."""
        await self.set_session(session_id=session_id, history=history, ttl_seconds=ttl_seconds)

    async def health_check(self) -> bool:
        try:
            client = self._require_client()
            await client.ping()
            return True
        except Exception:
            return False
