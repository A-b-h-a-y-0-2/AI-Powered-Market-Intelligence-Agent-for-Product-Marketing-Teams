"""Text-embedding wrapper with automatic local fallback.

Priority order:
  1. API provider (OpenAI or OpenRouter) — when api_key is set
  2. Local fastembed (BAAI/bge-small-en-v1.5, 384 dims) — when no api_key

Provider selection via constructor args:
  - api_key: optional API key; omit to use local fastembed
  - base_url: optional; defaults to EMBEDDING_BASE_URL env var, then OpenAI
  - model: optional; defaults to EMBEDDING_MODEL

OpenRouter example:
    Embedder(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        model="openai/text-embedding-3-small",
    )

Local fallback (no api_key):
    Embedder(api_key="")  →  fastembed bge-small-en-v1.5, 384 dims
"""

from __future__ import annotations

import asyncio
import os
from functools import partial

from openai import AsyncOpenAI

from observability.logger import get_logger
from tools.errors import ErrorCode, StorageError

log = get_logger("embedder")

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536  # API providers (OpenAI / OpenRouter)

LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_DIMENSIONS = 384  # fastembed bge-small output size

# Models that support the `dimensions` parameter (OpenAI text-embedding-3 family).
_SUPPORTS_DIMENSIONS = {
    "text-embedding-3-small", "text-embedding-3-large",
    "openai/text-embedding-3-small", "openai/text-embedding-3-large",
}


class Embedder:
    """Async embedding client with automatic local fastembed fallback.

    When api_key is empty, uses fastembed (BAAI/bge-small-en-v1.5, 384 dims)
    so deduplication and semantic search work without any external API.

    When api_key is set, uses the OpenAI-compatible API at base_url.
    """

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 30,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._local = not api_key  # use fastembed when no API key
        if self._local:
            self._fastembed_model = None  # lazy-loaded on first call
            self._client = None
            self._model = LOCAL_MODEL
            self._supports_dimensions = False
            self.dimensions = LOCAL_DIMENSIONS
            log.info(
                "embedder_local_mode",
                action="startup",
                model=LOCAL_MODEL,
                dims=LOCAL_DIMENSIONS,
                reason="No embedding API key — using local fastembed",
            )
            return

        resolved_base_url = base_url or os.environ.get("EMBEDDING_BASE_URL")
        self._fastembed_model = None
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            **({"base_url": resolved_base_url} if resolved_base_url else {}),
        )
        self._model = model or EMBEDDING_MODEL
        self._supports_dimensions = self._model in _SUPPORTS_DIMENSIONS
        self.dimensions = EMBEDDING_DIMENSIONS

    def _get_local_model(self):
        """Lazy-load the fastembed model (downloads ~33MB on first use)."""
        if self._fastembed_model is None:
            from fastembed import TextEmbedding
            self._fastembed_model = TextEmbedding(LOCAL_MODEL)
        return self._fastembed_model

    def _embed_local_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous fastembed call — run via executor to avoid blocking."""
        model = self._get_local_model()
        return [vec.tolist() for vec in model.embed(texts)]

    async def embed(self, text: str) -> list[float]:
        """Return embedding for the given text.

        - Local mode: 384-dim fastembed vector (bge-small-en-v1.5)
        - API mode:   1536-dim OpenAI/OpenRouter vector
        """
        if not text or not text.strip():
            raise StorageError(
                code=ErrorCode.EMBED_FAILED,
                message="Cannot embed empty text",
                context={"text_length": len(text)},
            )

        if self._local:
            try:
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(
                    None, partial(self._embed_local_sync, [text.strip()])
                )
                return results[0]
            except Exception as exc:
                raise StorageError(
                    code=ErrorCode.EMBED_FAILED,
                    message=f"Local embedding failed: {exc}",
                    context={"model": LOCAL_MODEL},
                    cause=exc,
                ) from exc

        try:
            kwargs: dict = {"model": self._model, "input": text.strip()}
            if self._supports_dimensions:
                kwargs["dimensions"] = EMBEDDING_DIMENSIONS
            response = await self._client.embeddings.create(**kwargs)
            return response.data[0].embedding
        except Exception as exc:
            log.error(
                "embed_failed",
                error_code=ErrorCode.EMBED_FAILED,
                action="embed",
                model=self._model,
                error=str(exc),
            )
            raise StorageError(
                code=ErrorCode.EMBED_FAILED,
                message=f"Embedding failed: {exc}",
                context={"model": self._model, "text_length": len(text)},
                cause=exc,
            ) from exc

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts efficiently."""
        if not texts:
            return []

        clean_texts = [t.strip() for t in texts if t and t.strip()]
        if not clean_texts:
            raise StorageError(
                code=ErrorCode.EMBED_FAILED,
                message="No non-empty texts to embed",
            )

        if self._local:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None, partial(self._embed_local_sync, clean_texts)
                )
            except Exception as exc:
                raise StorageError(
                    code=ErrorCode.EMBED_FAILED,
                    message=f"Local batch embedding failed: {exc}",
                    context={"model": LOCAL_MODEL, "batch_size": len(clean_texts)},
                    cause=exc,
                ) from exc

        try:
            kwargs: dict = {"model": self._model, "input": clean_texts}
            if self._supports_dimensions:
                kwargs["dimensions"] = EMBEDDING_DIMENSIONS
            response = await self._client.embeddings.create(**kwargs)
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [item.embedding for item in sorted_data]
        except Exception as exc:
            raise StorageError(
                code=ErrorCode.EMBED_FAILED,
                message=f"Batch embedding failed: {exc}",
                context={"model": self._model, "batch_size": len(clean_texts)},
                cause=exc,
            ) from exc
