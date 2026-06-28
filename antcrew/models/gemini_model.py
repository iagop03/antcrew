from __future__ import annotations

import os
from typing import Optional

import httpx

from antcrew.models.base import BaseLLM, Message

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiModel(BaseLLM):
    """
    Google Gemini model via REST API (no extra SDK required â€” uses httpx).

    Default model: gemini-1.5-flash

    Usage:
        llm = GeminiModel()                             # reads GOOGLE_API_KEY
        llm = GeminiModel("gemini-1.5-pro", api_key="...")
        llm = GeminiModel("gemini-2.0-flash")
    """

    def __init__(
        self,
        model: str = "gemini-1.5-flash",
        api_key: Optional[str] = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not self._api_key:
            raise EnvironmentError(
                "GOOGLE_API_KEY is not set.\n"
                "  export GOOGLE_API_KEY=AIza...\n"
                "  Get your key at: https://aistudio.google.com/app/apikey\n"
                "  Or use SimulatedLLM for testing without an API key."
            )

    def _build_body(self, messages: list[Message], max_tokens: int) -> dict:
        system_parts: list[str] = []
        contents: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        body: dict = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return body

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        body = self._build_body(messages, max_tokens)
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        if self.on_token:
            return self._with_retry(self._stream_complete, body)
        return self._with_retry(self._blocking_complete, body)

    def _blocking_complete(self, body: dict) -> str:
        resp = self._with_retry(
            httpx.post,
            f"{_BASE_URL}/{self._model}:generateContent",
            params={"key": self._api_key},
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("usageMetadata", {})
        self._record_usage(
            meta.get("promptTokenCount", 0),
            meta.get("candidatesTokenCount", 0),
        )
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def _stream_complete(self, body: dict) -> str:
        import json as _json
        chunks: list[str] = []
        in_tok = out_tok = 0
        with httpx.stream(
            "POST",
            f"{_BASE_URL}/{self._model}:streamGenerateContent",
            params={"key": self._api_key, "alt": "sse"},
            json=body,
            timeout=self.timeout,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw in ("", "[DONE]"):
                    continue
                try:
                    data = _json.loads(raw)
                    text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    if text:
                        self.on_token(text)  # type: ignore[misc]
                        chunks.append(text)
                    meta = data.get("usageMetadata", {})
                    if meta:
                        in_tok = meta.get("promptTokenCount", in_tok)
                        out_tok = meta.get("candidatesTokenCount", out_tok)
                except (_json.JSONDecodeError, IndexError, KeyError):
                    continue
        self._record_usage(in_tok, out_tok)
        return "".join(chunks)
