from __future__ import annotations

import httpx

from antcrew_engine.models.base import BaseLLM, Message

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3"


class OllamaModel(BaseLLM):
    """
    Adapter for Ollama's local model server.
    Requires Ollama running at base_url (default http://localhost:11434).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        *,
        timeout: float = 300.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout  # overrides BaseLLM.timeout (600s) with Ollama-appropriate default (300s)

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        import json as _json

        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": bool(self.on_token),
            "options": {"num_predict": max_tokens},
        }

        _to = self.timeout

        if self.on_token:
            chunks: list[str] = []
            in_tok = out_tok = 0
            with httpx.stream(
                "POST", f"{self.base_url}/api/chat",
                json=payload, timeout=_to,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = _json.loads(line)
                    text = data.get("message", {}).get("content", "")
                    if text:
                        self.on_token(text)
                        chunks.append(text)
                    if data.get("done"):
                        in_tok = data.get("prompt_eval_count", 0)
                        out_tok = data.get("eval_count", 0)
            self._record_usage(in_tok, out_tok)
            return "".join(chunks)

        response = httpx.post(
            f"{self.base_url}/api/chat", json=payload, timeout=_to,
        )
        response.raise_for_status()
        data = response.json()
        self._record_usage(
            data.get("prompt_eval_count", 0),
            data.get("eval_count", 0),
        )
        return data["message"]["content"]

    def is_available(self) -> bool:
        """Return True if the Ollama server responds."""
        try:
            httpx.get(f"{self.base_url}/api/tags", timeout=5.0).raise_for_status()
            return True
        except Exception:
            return False
