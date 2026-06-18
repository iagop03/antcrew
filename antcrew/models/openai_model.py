from __future__ import annotations

import os
from typing import Optional

from antcrew.models.base import BaseLLM, Message

try:
    from openai import OpenAI  # type: ignore[import]
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]


class OpenAIModel(BaseLLM):
    """
    OpenAI chat completion model.

    Default model: gpt-4o

    Usage:
        llm = OpenAIModel()                        # reads OPENAI_API_KEY
        llm = OpenAIModel("gpt-4o-mini", api_key="sk-...")
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        if OpenAI is None:
            raise ImportError(
                "openai package is required for OpenAIModel. "
                "Install it: pip install openai"
            )
        self._model = model
        self._client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", "not-needed"),
            **({"base_url": base_url} if base_url else {}),
        )

    def complete(self, messages: list[Message], *, max_tokens: int = 4096) -> str:
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages]

        if self.on_token:
            stream = self._client.chat.completions.create(
                model=self._model, messages=chat_msgs, max_tokens=max_tokens,
                stream=True, stream_options={"include_usage": True},
                timeout=self.timeout,
            )
            chunks: list[str] = []
            usage_data = None
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    self.on_token(text)
                    chunks.append(text)
                if getattr(chunk, "usage", None):
                    usage_data = chunk.usage
            if usage_data:
                self._record_usage(usage_data.prompt_tokens, usage_data.completion_tokens)
            return "".join(chunks)

        response = self._client.chat.completions.create(
            model=self._model, messages=chat_msgs, max_tokens=max_tokens,
            timeout=self.timeout,
        )
        if response.usage:
            self._record_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content or ""
