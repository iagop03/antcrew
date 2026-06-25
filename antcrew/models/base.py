from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Literal, Optional
from pydantic import BaseModel

if TYPE_CHECKING:
    from antcrew.models.cache import LLMCache


def _is_complete_response(text: str) -> bool:
    """Return False for responses that look truncated mid-JSON.

    A cached response that ends inside an unterminated string or mid-object
    was almost certainly cut off by a max_tokens limit.  Rejecting it forces
    a fresh API call so the caller gets the full response.
    """
    stripped = text.strip()
    if not stripped:
        return False
    # Only validate if the response looks like JSON (starts with { or [)
    if stripped[0] not in ("{", "["):
        return True
    try:
        json.loads(stripped)
        return True
    except json.JSONDecodeError:
        return False


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


# ---------------------------------------------------------------------------
# Cost table — (prefix, input_per_1M_USD, output_per_1M_USD)
# Matched by substring of the lowercase model name.
# ---------------------------------------------------------------------------
_COST_TABLE: list[tuple[str, float, float]] = [
    ("claude-opus",      15.00, 75.00),
    ("claude-sonnet",     3.00, 15.00),
    ("claude-haiku",      0.25,  1.25),
    ("gpt-4o-mini",       0.15,  0.60),
    ("gpt-4o",            2.50, 10.00),
    ("gemini-1.5-pro",    1.25,  5.00),
    ("gemini-1.5-flash",  0.075, 0.30),
    ("gemini-2.0",        0.075, 0.30),
    ("llama3-70b",        0.59,  0.79),
    ("llama3-8b",         0.05,  0.08),
    ("mixtral",           0.24,  0.24),
    ("deepseek",          0.14,  0.28),
    ("mistral",           0.20,  0.60),
]

# HTTP status codes that warrant a retry
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# httpx / SDK exception class names that are retryable
_RETRYABLE_EXC_NAMES = frozenset({
    "TimeoutException", "ConnectError", "RemoteProtocolError",
    "ReadTimeout", "WriteTimeout", "PoolTimeout",
})


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    for obj in (exc, getattr(exc, "response", None)):
        if getattr(obj, "status_code", None) in _RETRYABLE_STATUS:
            return True
    if type(exc).__name__ in _RETRYABLE_EXC_NAMES:
        return True
    if type(exc).__module__.startswith("httpx"):
        return True
    return False


class BaseLLM(ABC):
    """Abstract base for all LLM adapters.

    Instance-level attributes you can override after construction:

        llm.max_retries  = 5      # retry attempts (non-streaming only; default 3)
        llm.retry_delay  = 2.0    # initial backoff in seconds (doubles each attempt)
        llm.timeout      = 60.0   # HTTP timeout in seconds (default 120)
        llm.on_token     = fn     # called with each streaming text chunk
        llm.current_agent = "pm"  # set automatically by BaseAgent.system()
        llm.max_cost_usd = 2.0    # abort run when this cost (USD) is exceeded
    """

    # Streaming
    on_token: Optional[Callable[[str], None]] = None
    current_agent: str = ""

    # Retry / timeout
    max_retries: int = 3
    retry_delay: float = 1.0
    timeout: float = 600.0

    # Prompt cache (opt-in — assign an LLMCache instance to enable)
    cache: "Optional[LLMCache]" = None

    # Cost guard — set by team when max_cost_usd is configured
    max_cost_usd: Optional[float] = None
    _cost_limit_offset: float = 0.0  # accumulated cost at the start of the current run

    # ── Usage tracking ──────────────────────────────────────────────────────

    @property
    def _usage_log(self) -> list[dict]:
        """Per-instance log; lazily created to avoid shared mutable class attr."""
        if "_usage_log_impl" not in self.__dict__:
            self.__dict__["_usage_log_impl"] = []
        return self.__dict__["_usage_log_impl"]

    def _model_name(self) -> str:
        return getattr(self, "_model", getattr(self, "model", "")).lower()

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        name = self._model_name()
        for prefix, in_cost, out_cost in _COST_TABLE:
            if prefix in name:
                return (input_tokens * in_cost + output_tokens * out_cost) / 1_000_000
        return 0.0

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Call this at the end of every successful complete() to track usage."""
        self._usage_log.append({
            "agent":         self.current_agent,
            "model":         self._model_name() or "unknown",
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "cost_usd":      round(self._estimate_cost(input_tokens, output_tokens), 6),
        })

    def get_usage_summary(self) -> dict:
        """Aggregated token counts and estimated cost across all calls."""
        log = self._usage_log
        if not log:
            return {
                "total_input_tokens":  0,
                "total_output_tokens": 0,
                "total_cost_usd":      0.0,
                "by_agent":            [],
            }
        return {
            "total_input_tokens":  sum(e["input_tokens"]  for e in log),
            "total_output_tokens": sum(e["output_tokens"] for e in log),
            "total_cost_usd":      round(sum(e["cost_usd"] for e in log), 6),
            "by_agent":            list(log),
        }

    # ── Retry ────────────────────────────────────────────────────────────────

    def _with_retry(self, fn, *args, **kwargs):
        """Call fn(*args, **kwargs) with exponential-backoff retry on transient errors."""
        last_exc: BaseException = RuntimeError("unreachable")
        for attempt in range(self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retries or not _is_retryable(exc):
                    raise
                time.sleep(self.retry_delay * (2 ** attempt))
        raise last_exc  # pragma: no cover

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def complete(self, messages: list[Message], *, max_tokens: int = 16384) -> str:
        """Send messages and return the full reply as a string.

        When ``self.on_token`` is set, stream the response and call
        ``on_token(chunk)`` for each piece of text; still return the full
        concatenated string at the end.

        Must call ``self._record_usage(input_tokens, output_tokens)`` after
        every successful call.
        """

    def system(self, prompt: str, user: str, **kwargs) -> str:
        """One system + one user message, with cache and optional streaming."""
        if self.max_cost_usd is not None:
            spent = self.get_usage_summary()["total_cost_usd"] - self._cost_limit_offset
            if spent >= self.max_cost_usd:
                from antcrew.core.exceptions import CostLimitExceeded
                raise CostLimitExceeded(spent, self.max_cost_usd)
        messages = [
            Message(role="system", content=prompt),
            Message(role="user", content=user),
        ]
        cache = getattr(self, "cache", None)
        agent = getattr(self, "current_agent", "") or ""
        model = type(self).__name__
        # Check cache first, even in streaming mode — a cache hit skips the API entirely.
        if cache is not None:
            hit = cache.get(messages, model, validate=_is_complete_response, agent_name=agent)
            if hit is not None:
                return hit
        if self.on_token is not None:
            # Streaming path: call complete() live, then persist result to cache.
            result = self.complete(messages, **kwargs)
            if cache is not None:
                cache.set(messages, model, result, agent_name=agent)
            return result
        if cache is not None:
            result = self._with_retry(self.complete, messages, **kwargs)
            cache.set(messages, model, result, agent_name=agent)
            return result
        return self._with_retry(self.complete, messages, **kwargs)

    def with_cache(self, cache=None) -> "BaseLLM":
        """Attach a prompt cache to this model and return self.

        Repeated calls with the same prompt skip the API entirely.
        Streaming calls are never cached.

        Args:
            cache: One of:
                - None (default) → create a new in-memory LLMCache
                - str or Path    → create a FileLLMCache at that path (SQLite)
                - LLMCache       → use the provided cache instance

        Example:
            # In-memory
            llm = AnthropicModel().with_cache()
            # Persistent across restarts
            llm = AnthropicModel().with_cache("~/.antcrew/cache.db")
        """
        import os
        from antcrew.models.cache import LLMCache as _LLMCache
        if isinstance(cache, (str, os.PathLike)):
            from antcrew.models.cache import FileLLMCache as _FC
            self.cache = _FC(cache)
        elif cache is not None:
            self.cache = cache
        else:
            self.cache = _LLMCache()
        return self

    def with_fallback(self, *fallbacks: "BaseLLM") -> "BaseLLM":
        """Return a FallbackLLM that tries self first, then each fallback in order.

        Example:
            llm = AnthropicModel("claude-sonnet-4-6").with_fallback(
                OpenAIModel("gpt-4o-mini"),
                GeminiModel("gemini-2.0-flash"),
            )
        """
        from antcrew.models.fallback import FallbackLLM
        return FallbackLLM([self, *fallbacks])
