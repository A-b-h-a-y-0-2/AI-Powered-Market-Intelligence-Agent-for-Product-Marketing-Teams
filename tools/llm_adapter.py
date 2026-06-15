"""LLM provider adapter — routes model calls to Groq or OpenRouter.

Provider routing rules:
  Groq     → Llama 3.x, Mixtral, Gemma, Qwen, Whisper models
  OpenRouter → Claude (Anthropic), GPT-4, Gemini, and any other models

OpenRouter exposes an OpenAI-compatible API, so `instructor.from_openai` works
transparently. No Anthropic SDK required.

Usage:
    adapter = LLMAdapter(
        groq_api_key=os.environ["GROQ_API_KEY"],
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
    )

    # Instructor-wrapped (structured output):
    client = adapter.get_instructor_client("anthropic/claude-sonnet-4-6")
    result = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-6",
        messages=[...],
        response_model=MySchema,
    )

    # Raw (streaming / plain text):
    client = adapter.get_chat_client("anthropic/claude-sonnet-4-6")
    response = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-6",
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content
"""

from __future__ import annotations

import instructor
from openai import OpenAI

from tools.errors import ConfigError, ErrorCode

# Model name prefixes that are served by Groq
_GROQ_PREFIXES = ("llama", "mixtral", "gemma", "qwen", "whisper", "deepseek")

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMAdapter:
    """Routes LLM calls to Groq or OpenRouter based on model name.

    Both providers expose an OpenAI-compatible API, so `instructor.from_openai`
    works for both. Groq also has its own SDK but OpenAI-compat is simpler here.

    Clients are created lazily and cached — one per provider.
    """

    def __init__(self, groq_api_key: str, openrouter_api_key: str) -> None:
        if not groq_api_key:
            raise ConfigError(
                code=ErrorCode.CONFIG_MISSING_FIELD,
                message="GROQ_API_KEY is required for LLMAdapter",
            )
        if not openrouter_api_key:
            raise ConfigError(
                code=ErrorCode.CONFIG_MISSING_FIELD,
                message="OPENROUTER_API_KEY is required for LLMAdapter",
            )
        self._groq_api_key = groq_api_key
        self._openrouter_api_key = openrouter_api_key
        self._groq_client: OpenAI | None = None
        self._openrouter_client: OpenAI | None = None
        self._groq_instructor: instructor.Instructor | None = None
        self._openrouter_instructor: instructor.Instructor | None = None

    # ── Provider detection ────────────────────────────────────────────────────

    @staticmethod
    def is_groq_model(model_name: str) -> bool:
        return any(model_name.lower().startswith(p) for p in _GROQ_PREFIXES)

    # ── Raw OpenAI-compatible clients ─────────────────────────────────────────

    def _groq_chat_client(self) -> OpenAI:
        if self._groq_client is None:
            self._groq_client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self._groq_api_key,
            )
        return self._groq_client

    def _openrouter_chat_client(self) -> OpenAI:
        if self._openrouter_client is None:
            self._openrouter_client = OpenAI(
                base_url=_OPENROUTER_BASE_URL,
                api_key=self._openrouter_api_key,
            )
        return self._openrouter_client

    def get_chat_client(self, model_name: str) -> OpenAI:
        """Return a raw OpenAI-compatible client for plain text / streaming calls."""
        return (
            self._groq_chat_client()
            if self.is_groq_model(model_name)
            else self._openrouter_chat_client()
        )

    # ── Instructor-wrapped clients ────────────────────────────────────────────

    def get_instructor_client(self, model_name: str) -> instructor.Instructor:
        """Return an Instructor-patched client for structured output calls."""
        if self.is_groq_model(model_name):
            if self._groq_instructor is None:
                self._groq_instructor = instructor.from_openai(
                    self._groq_chat_client(),
                    mode=instructor.Mode.JSON,
                )
            return self._groq_instructor
        else:
            if self._openrouter_instructor is None:
                self._openrouter_instructor = instructor.from_openai(
                    self._openrouter_chat_client(),
                    mode=instructor.Mode.JSON,
                )
            return self._openrouter_instructor

    # ── Helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def build_messages(system: str, user: str) -> list[dict]:
        """Construct a standard two-turn message list."""
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
