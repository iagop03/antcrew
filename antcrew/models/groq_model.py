from __future__ import annotations

import os
from typing import Optional

from groq import Groq

from antcrew.models.base import BaseLLM, Message

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

    def complete(self, messages: list[Message], *, max_tokens: int = 8192) -> str:
        chat_msgs = [{"role": m.role, "content": m.content} for m in messages]

        if self.on_token:
            stream = self._client.chat.completions.create(
                model=self.model, messages=chat_msgs,
                max_tokens=max_tokens, stream=True,
            )
            chunks: list[str] = []
            for chunk in stream:
                text = chunk.choices[0].delta.content or ""
                if text:
                    self.on_token(text)
                    chunks.append(text)
            # Groq streaming doesn't return usage; approximate from text
            full = "".join(chunks)
            self._record_usage(len(full) // 4, len(full) // 4)
            return full

        response = self._client.chat.completions.create(
            model=self.model, messages=chat_msgs, max_tokens=max_tokens,
        )
        if response.usage:
            self._record_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content
