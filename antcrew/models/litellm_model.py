"""LiteLLM model adapter — supports 100+ providers with one interface.

Install: pip install antcrew[litellm]

Supported model strings (examples):
    "openai/gpt-4o"
    "anthropic/claude-opus-4"
    "ollama/llama3.2"
    "bedrock/claude-3-5-sonnet-20241022"
    "groq/mixtral-8x7b-32768"
    "together_ai/meta-llama/Llama-3-70b"
    "vertex_ai/gemini-2.0-flash"
    "azure/gpt-4o"

See https://docs.litellm.ai/docs/providers for the full list.
"""
from __future__ import annotations

from typing import Optional

try:
    import litellm as _litellm
    _AVAILABLE = True
except ImportError:
    _litellm = None  # type: ignore[assignment]
    _AVAILABLE = False

from antcrew_engine.models.base import BaseLLM, Message


class LiteLLMModel(BaseLLM):
    """LLM backend backed by LiteLLM — supports 100+ providers / models.

    Examples::

        from antcrew.models.litellm_model import LiteLLMModel

        llm = LiteLLMModel("openai/gpt-4o")
        llm = LiteLLMModel("ollama/llama3.2", api_base="http://localhost:11434")
        llm = LiteLLMModel("groq/mixtral-8x7b-32768")
        llm = LiteLLMModel("bedrock/claude-3-5-sonnet-20241022", aws_region_name="us-east-1")

    Token usage is tracked normally via :meth:`BaseLLM._record_usage`.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        **extra_kwargs,
    ) -> None:
        if not _AVAILABLE:
            raise ImportError(
                "litellm is required for LiteLLMModel.\n"
                "  pip install antcrew[litellm]\n"
                "  or: pip install litellm"
            )
        self.model = model
        self._api_key = api_key
        self._api_base = api_base
        self._extra = extra_kwargs

    # ------------------------------------------------------------------
    # BaseLLM interface
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 16384,
        json_mode: bool = False,
    ) -> str:
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict = {
            "model": self.model,
            "messages": chat_msgs,
            "max_tokens": max_tokens,
            **self._extra,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if self.on_token:
            kwargs["stream"] = True
            return self._with_retry(self._stream_complete, kwargs)
        return self._with_retry(self._blocking_complete, kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _blocking_complete(self, kwargs: dict) -> str:
        response = _litellm.completion(**kwargs)
        usage = getattr(response, "usage", None)
        if usage:
            self._record_usage(
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
            )
        return response.choices[0].message.content or ""

    def _stream_complete(self, kwargs: dict) -> str:
        chunks: list[str] = []
        for chunk in _litellm.completion(**kwargs):
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if delta:
                chunks.append(delta)
                if self.on_token:
                    self.on_token(delta)
        return "".join(chunks)
