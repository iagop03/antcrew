from __future__ import annotations

import os
from typing import Optional

import anthropic

from antcrew.models.base import BaseLLM, Message

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicModel(BaseLLM):
    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: Optional[str] = None,
    ) -> None:
        self.model = model
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set.\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  Get your key at: https://console.anthropic.com\n"
                "  Or use SimulatedLLM for testing without an API key."
            )
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, messages: list[Message], *, max_tokens: int = 4096) -> str:
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        kwargs["timeout"] = self.timeout

        if self.on_token:
            chunks: list[str] = []
            with self._client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    self.on_token(text)
                    chunks.append(text)
                usage = stream.get_final_message().usage
                self._record_usage(usage.input_tokens, usage.output_tokens)
            return "".join(chunks)

        response = self._client.messages.create(**kwargs)
        self._record_usage(response.usage.input_tokens, response.usage.output_tokens)
        return response.content[0].text
