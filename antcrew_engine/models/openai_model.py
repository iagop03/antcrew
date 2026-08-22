from __future__ import annotations

import os
from typing import Optional

from antcrew_engine.models.base import BaseLLM, Message

try:
    from openai import OpenAI  # type: ignore[import]
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]


# Reasoning models use max_completion_tokens and do not support streaming.
_REASONING_PREFIXES = ("o1", "o3")


def _is_reasoning_model(model: str) -> bool:
    return any(model.lower().startswith(p) for p in _REASONING_PREFIXES)


class OpenAIModel(BaseLLM):
    # Default; overridden in __init__ based on the chosen model.
    _is_reasoning: bool = False

    """OpenAI chat / reasoning model adapter.

    Supports:
    - Chat models: ``gpt-4o``, ``gpt-4o-mini``, ``gpt-3.5-turbo``, …
    - Reasoning models: ``o1``, ``o1-mini``, ``o3``, ``o3-mini``

    Reasoning models (``o1``/``o3`` family) automatically use
    ``max_completion_tokens`` instead of ``max_tokens`` and skip streaming
    because the OpenAI API does not support it for those models.

    Usage::

        llm = OpenAIModel()                        # reads OPENAI_API_KEY, default gpt-4o
        llm = OpenAIModel("o3-mini")               # reasoning model, no streaming
        llm = OpenAIModel("gpt-4o-mini", api_key="sk-...")
        llm = OpenAIModel("gpt-4o", base_url="https://your-proxy/v1")
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        organization: Optional[str] = None,
    ) -> None:
        if OpenAI is None:
            raise ImportError(
                "openai package is required for OpenAIModel. "
                "Install it: pip install antcrew[openai]"
            )
        self._model = model
        self._is_reasoning = _is_reasoning_model(model)
        client_kwargs: dict = {
            "api_key": api_key or os.environ.get("OPENAI_API_KEY", "not-needed"),
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        if organization:
            client_kwargs["organization"] = organization
        self._client = OpenAI(**client_kwargs)

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages]

        # Reasoning models: no streaming, different token param name.
        if self._is_reasoning:
            return self._complete_reasoning(chat_msgs, max_tokens)

        if self.on_token:
            return self._complete_streaming(chat_msgs, max_tokens)

        return self._complete_blocking(chat_msgs, max_tokens, json_mode=json_mode)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_content(self, response, *, context: str) -> str:
        """Extract text content from a completions response with a developer-friendly error.

        Raw IndexError/AttributeError here means the provider is not fully
        OpenAI-compatible or returned an unexpected shape (empty choices, tool-call-only
        response, HTML error page parsed as JSON, etc.).
        """
        choices = getattr(response, "choices", [])
        if not choices:
            raw = repr(response)[:600]
            raise ValueError(
                f"OpenAIModel({self._model!r}) — provider returned 0 choices ({context}).\n"
                f"Common causes:\n"
                f"  • Model ID '{self._model}' is not available at this endpoint\n"
                f"  • base_url points to a non-OpenAI-compatible API or returns an error page\n"
                f"  • API key was rejected before any output was generated\n"
                f"  • Provider returned a non-text response (tool calls, refusal, empty stream)\n"
                f"Provider response: {raw}"
            )
        return choices[0].message.content or ""

    def _complete_blocking(self, chat_msgs: list[dict], max_tokens: int, *, json_mode: bool = False) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": chat_msgs,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._with_retry(self._client.chat.completions.create, **kwargs)
        if response.usage:
            self._record_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
        return self._parse_content(response, context="blocking")

    def _complete_streaming(self, chat_msgs: list[dict], max_tokens: int) -> str:
        def _do_stream():
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=chat_msgs,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                timeout=self.timeout,
            )
            chunks: list[str] = []
            usage_data = None
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    if self.on_token:
                        self.on_token(text)
                    chunks.append(text)
                if getattr(chunk, "usage", None):
                    usage_data = chunk.usage
            if usage_data:
                self._record_usage(
                    usage_data.prompt_tokens,
                    usage_data.completion_tokens,
                )
            return "".join(chunks)

        return self._with_retry(_do_stream)

    def _complete_reasoning(self, chat_msgs: list[dict], max_tokens: int) -> str:
        response = self._with_retry(
            self._client.chat.completions.create,
            model=self._model,
            messages=chat_msgs,
            max_completion_tokens=max_tokens,
            timeout=self.timeout,
        )
        if response.usage:
            self._record_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
        return self._parse_content(response, context="reasoning")
