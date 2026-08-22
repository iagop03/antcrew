from __future__ import annotations

import os
from typing import Optional

try:
    from groq import Groq  # type: ignore[import]
except ImportError:
    Groq = None  # type: ignore[assignment,misc]

from antcrew_engine.models.base import BaseLLM, Message

_DEFAULT_MODEL = "llama3-70b-8192"


class GroqModel(BaseLLM):
    """
    Adapter for Groq's ultra-fast inference API.
    Compatible with Llama 3, Mixtral, Gemma and other models hosted on Groq.
    Requires a GROQ_API_KEY environment variable (or explicit api_key).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: Optional[str] = None,
    ) -> None:
        if Groq is None:
            raise ImportError(
                "groq package is required for GroqModel. "
                "Install it: pip install antcrew-engine[groq]"
            )
        self.model = model
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise EnvironmentError(
                "GROQ_API_KEY is not set.\n"
                "  export GROQ_API_KEY=gsk_...\n"
                "  Get your key at: https://console.groq.com\n"
                "  Or use SimulatedLLM for testing without an API key."
            )
        self._client = Groq(api_key=key)

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages]

        if self.on_token:
            return self._with_retry(self._stream_complete, chat_msgs, max_tokens)
        return self._with_retry(self._blocking_complete, chat_msgs, max_tokens, json_mode)

    def _blocking_complete(self, chat_msgs: list[dict], max_tokens: int, json_mode: bool = False) -> str:
        kwargs: dict = {"model": self.model, "messages": chat_msgs, "max_tokens": max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._with_retry(self._client.chat.completions.create, **kwargs)
        if response.usage:
            self._record_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content

    def _stream_complete(self, chat_msgs: list[dict], max_tokens: int) -> str:
        stream = self._client.chat.completions.create(
            model=self.model, messages=chat_msgs,
            max_tokens=max_tokens, stream=True,
        )
        chunks: list[str] = []
        for chunk in stream:
            text = chunk.choices[0].delta.content or ""
            if text:
                self.on_token(text)  # type: ignore[misc]
                chunks.append(text)
        full = "".join(chunks)
        # Groq streaming doesn't return usage; approximate from character count
        self._record_usage(len(full) // 4, len(full) // 4)
        return full
