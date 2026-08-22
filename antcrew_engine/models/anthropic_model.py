from __future__ import annotations

import os
from typing import Optional

import anthropic

from antcrew_engine.models.base import BaseLLM, Message

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicModel(BaseLLM):
    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: Optional[str] = None,
        prompt_caching: bool = False,
        base_url: Optional[str] = None,
    ) -> None:
        self.model = model
        self.prompt_caching = prompt_caching
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set.\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  Get your key at: https://console.anthropic.com\n"
                "  Or use SimulatedLLM for testing without an API key."
            )
        headers: dict = {}
        if prompt_caching:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
        client_kw: dict = {"api_key": key}
        if base_url:
            client_kw["base_url"] = base_url
        if headers:
            client_kw["default_headers"] = headers
        self._client = anthropic.Anthropic(**client_kw)

    def _build_system(self, system_parts: list[str]):
        """Return the system value for the API call.

        Plain string when caching is off; a content-block list with
        cache_control on the last block when caching is on.
        """
        text = "\n\n".join(system_parts)
        if not self.prompt_caching:
            return text
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    @staticmethod
    def _total_input(usage) -> int:
        """Sum all input-token variants (plain + cache write + cache read)."""
        return (
            getattr(usage, "input_tokens", 0)
            + getattr(usage, "cache_creation_input_tokens", 0)
            + getattr(usage, "cache_read_input_tokens", 0)
        )

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
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
            "timeout": self.timeout,
        }
        if system_parts:
            kwargs["system"] = self._build_system(system_parts)

        if self.on_token:
            return self._with_retry(self._stream_complete, kwargs)
        return self._with_retry(self._blocking_complete, kwargs)

    def _blocking_complete(self, kwargs: dict) -> str:
        response = self._client.messages.create(**kwargs)
        self._record_usage(
            self._total_input(response.usage),
            response.usage.output_tokens,
            cache_read=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )
        if response.stop_reason == "max_tokens":
            u = response.usage
            raise RuntimeError(
                f"Response truncated: hit max_tokens={kwargs.get('max_tokens')} "
                f"(input={u.input_tokens}, output={u.output_tokens}). "
                "Increase max_tokens or shorten the input."
            )
        return response.content[0].text

    def _stream_complete(self, kwargs: dict) -> str:
        chunks: list[str] = []
        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                self.on_token(text)  # type: ignore[misc]
                chunks.append(text)
            final = stream.get_final_message()
            self._record_usage(
                self._total_input(final.usage),
                final.usage.output_tokens,
                cache_read=getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                cache_write=getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
            )
            if final.stop_reason == "max_tokens":
                u = final.usage
                raise RuntimeError(
                    f"Response truncated: hit max_tokens={kwargs.get('max_tokens')} "
                    f"(input={u.input_tokens}, output={u.output_tokens}). "
                    "Increase max_tokens or shorten the input."
                )
        return "".join(chunks)
