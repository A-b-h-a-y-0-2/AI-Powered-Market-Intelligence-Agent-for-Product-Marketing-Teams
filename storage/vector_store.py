"""Supabase pgvector store for semantic retrieval.

Stores embeddings of event summaries for semantic search.
Always used with timestamp + company metadata filters — never pure vector search.
Hybrid retrieval pattern: WHERE company = X AND timestamp > Y ORDER BY cosine_similarity.

Raises StorageError with named codes on all failures.
"""

from __future__ import annotations

from typing import Any

from supabase import AsyncClient, acreate_client

from tools.errors import ErrorCode, StorageError

EVENTS_TABLE = "event_embeddings"


_PLACEHOLDER_URLS = {"https://placeholder.supabase.co", "", None}


class VectorStore:
    """Async Supabase pgvector client.

    When supabase_url is None or placeholder, operates in stub mode:
    upsert_embedding is a no-op and semantic_search returns [].
    This lets the system start without Supabase configured.
    """

    def __init__(self, supabase_url: str | None, supabase_key: str | None) -> None:
        self._url = supabase_url
        self._key = supabase_key
        self._client: AsyncClient | None = None
        self._stub = supabase_url in _PLACEHOLDER_URLS

    async def connect(self) -> None:
        if self._stub:
            return  # No-op in stub mode
        try:
            self._client = await acreate_client(self._url, self._key)
        except Exception as exc:
            raise StorageError(
                code=ErrorCode.STORE_CONNECTION_FAILED,
                message=f"Failed to connect to Supabase at {self._url}",
                cause=exc,
            ) from exc

    async def disconnect(self) -> None:
        self._client = None

    def _require_client(self) -> AsyncClient:
        if self._client is None:
            raise StorageError(
                code=ErrorCode.STORE_CONNECTION_FAILED,
                message="VectorStore not connected. Call connect() first.",
            )
        return self._client

    async def upsert_embedding(
        self,
        event_id: str,
        company: str,
        event_type: str,
        timestamp: str,
        summary: str,
        embedding: list[float],
        stakeholder_tags: list[str],
    ) -> None:
        """Insert or update an event embedding. No-op in stub mode."""
        if self._stub:
            return
        client = self._require_client()
        record = {
            "event_id": event_id,
            "company": company,
            "event_type": event_type,
            "timestamp": timestamp,
            "summary": summary,
            "embedding": embedding,
            "stakeholder_tags": stakeholder_tags,
        }
        try:
            await client.table(EVENTS_TABLE).upsert(record, on_conflict="event_id").execute()
        except Exception as exc:
            raise StorageError(
                code=ErrorCode.VECTOR_STORE_WRITE_FAILED,
                message=f"Failed to upsert embedding for event {event_id}",
                context={"event_id": event_id, "company": company},
                cause=exc,
            ) from exc

    async def semantic_search(
        self,
        query_embedding: list[float],
        company: str | None = None,
        event_types: list[str] | None = None,
        since_timestamp: str | None = None,
        stakeholder_tag: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval: filtered by metadata, ranked by cosine similarity.

        Returns [] in stub mode (no Supabase configured).
        """
        if self._stub:
            return []
        client = self._require_client()

        # Build RPC call to pgvector match function
        params: dict[str, Any] = {
            "query_embedding": query_embedding,
            "match_count": limit,
            "min_similarity": min_similarity,
        }
        if company:
            params["filter_company"] = company
        if event_types:
            params["filter_event_types"] = event_types
        if since_timestamp:
            params["filter_since"] = since_timestamp
        if stakeholder_tag:
            params["filter_stakeholder_tag"] = stakeholder_tag

        try:
            response = await client.rpc("match_event_embeddings", params).execute()
            return response.data or []
        except Exception as exc:
            raise StorageError(
                code=ErrorCode.VECTOR_STORE_READ_FAILED,
                message="Semantic search failed",
                context={"company": company, "limit": limit},
                cause=exc,
            ) from exc

    async def health_check(self) -> bool:
        if self._stub:
            return True  # stub mode is always "healthy"
        try:
            client = self._require_client()
            await client.table(EVENTS_TABLE).select("id").limit(1).execute()
            return True
        except Exception:
            return False
