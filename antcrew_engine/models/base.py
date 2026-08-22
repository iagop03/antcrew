from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Literal, Optional

from pydantic import BaseModel

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from antcrew.trace import TraceLog

    from antcrew_engine.context.compressor import ContextCompressor
    from antcrew_engine.models.cache import LLMCache


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
    # Anthropic — specific versions first so they win over generic prefix matches
    ("claude-opus-5",      15.00,  75.00),
    ("claude-opus-4",      15.00,  75.00),
    ("claude-sonnet-5",     3.00,  15.00),
    ("claude-sonnet-4",     3.00,  15.00),
    ("claude-haiku-4",      0.80,   4.00),   # claude-haiku-4-5 and above
    # Anthropic — generic fallbacks (Claude 3.x)
    ("claude-opus",        15.00,  75.00),
    ("claude-sonnet",       3.00,  15.00),
    ("claude-haiku",        0.25,   1.25),
    # OpenAI — reasoning models (match before gpt-4o / o3)
    ("o4-mini",             1.10,   4.40),
    ("o3-mini",             1.10,   4.40),
    ("o1-mini",             1.10,   4.40),
    ("o1",                 15.00,  60.00),
    ("o3",                 10.00,  40.00),
    # OpenAI — chat models
    ("gpt-4.1-mini",        0.40,   1.60),
    ("gpt-4.1-nano",        0.10,   0.40),
    ("gpt-4.1",             2.00,   8.00),
    ("gpt-4o-mini",         0.15,   0.60),
    ("gpt-4o",              2.50,  10.00),
    ("gpt-4-turbo",        10.00,  30.00),
    ("gpt-3.5",             0.50,   1.50),
    # Google — specific versions first
    ("gemini-2.5-pro",      1.25,  10.00),
    ("gemini-2.5-flash",    0.15,   0.60),
    ("gemini-2.0-flash",    0.10,   0.40),
    ("gemini-2.0",          0.10,   0.40),
    ("gemini-1.5-pro",      1.25,   5.00),
    ("gemini-1.5-flash",    0.075,  0.30),
    # Open-source / hosted
    ("llama3-70b",          0.59,   0.79),
    ("llama3-8b",           0.05,   0.08),
    ("mixtral",             0.24,   0.24),
    ("mistral",             0.20,   0.60),
    # Moonshot AI (Kimi) — CNY pricing converted to USD at ~7.25 CNY/USD
    ("moonshot-v1-8k",      1.65,   1.65),
    ("moonshot-v1-32k",     3.31,   3.31),
    ("moonshot-v1-128k",    8.28,   8.28),
    # DeepSeek — specific before generic
    ("deepseek-reasoner",   0.55,   2.19),
    ("deepseek-chat",       0.27,   1.10),
    ("deepseek",            0.27,   1.10),
    # Mistral AI
    ("mistral-large",       2.00,   6.00),
    ("codestral",           0.30,   0.90),
    # xAI Grok
    ("grok-3-mini",         0.30,   0.50),
    ("grok-3",              3.00,  15.00),
    ("grok-2-mini",         0.20,   0.40),
    ("grok-2",              2.00,  10.00),
    ("grok-beta",           5.00,  15.00),
    # Cerebras (fast inference)
    ("llama3.1-70b",        0.60,   0.60),
    ("llama3.1-8b",         0.10,   0.10),
    # Local providers (LM Studio, vLLM, Ollama) — no cloud cost
    # Together AI / Fireworks — open-source models at hosted prices; falls through to llama/mixtral matches above
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

        llm.max_retries    = 5     # retry attempts (default 3)
        llm.retry_delay    = 2.0   # initial backoff in seconds (doubles each attempt)
        llm.max_retry_delay = 60.0 # backoff ceiling in seconds (default 60)
        llm.retry_jitter   = 0.5   # uniform jitter added to each delay (default 0.5)
        llm.timeout        = 60.0  # HTTP timeout in seconds (default 600)
        llm.on_token       = fn    # called with each streaming text chunk
        llm.current_agent  = "pm"  # set automatically by BaseAgent.system()
        llm.max_cost_usd   = 2.0   # abort run when this cost (USD) is exceeded
    """

    # Streaming
    on_token: Optional[Callable[[str], None]] = None
    current_agent: str = ""

    # Retry / timeout
    max_retries: int = 3
    retry_delay: float = 1.0
    max_retry_delay: float = 60.0
    retry_jitter: float = 0.5
    timeout: float = 600.0

    # Prompt cache (opt-in — assign an LLMCache instance to enable)
    cache: "Optional[LLMCache]" = None

    # Cost guard — set by team when max_cost_usd is configured
    max_cost_usd: Optional[float] = None
    _cost_limit_offset: float = 0.0  # accumulated cost at the start of the current run

    # Trace — set by team when trace_log is attached
    trace: "Optional[TraceLog]" = None
    _trace_run_id: Optional[str] = None

    # Context compression — opt-in; when both are set, _maybe_compress() will
    # shrink variable context that exceeds budget_tokens before each call.
    context_compressor: "Optional[ContextCompressor]" = None
    context_budget_tokens: Optional[int] = None

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

    def _record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        """Call this at the end of every successful complete() to track usage."""
        self._usage_log.append({
            "agent":              self.current_agent,
            "model":              self._model_name() or "unknown",
            "input_tokens":       input_tokens,
            "output_tokens":      output_tokens,
            "cache_read_tokens":  cache_read,
            "cache_write_tokens": cache_write,
            "cost_usd":           round(self._estimate_cost(input_tokens, output_tokens), 6),
        })

    def get_usage_summary(self) -> dict:
        """Aggregated token counts and estimated cost across all calls."""
        log = self._usage_log
        if not log:
            return {
                "total_input_tokens":       0,
                "total_output_tokens":      0,
                "total_cache_read_tokens":  0,
                "total_cache_write_tokens": 0,
                "total_cost_usd":           0.0,
                "by_agent":                 [],
            }
        return {
            "total_input_tokens":       sum(e["input_tokens"]  for e in log),
            "total_output_tokens":      sum(e["output_tokens"] for e in log),
            "total_cache_read_tokens":  sum(e.get("cache_read_tokens",  0) for e in log),
            "total_cache_write_tokens": sum(e.get("cache_write_tokens", 0) for e in log),
            "total_cost_usd":           round(sum(e["cost_usd"] for e in log), 6),
            "by_agent":                 list(log),
        }

    # ── Retry ────────────────────────────────────────────────────────────────

    def _retry_delay_for(self, attempt: int, exc: BaseException) -> float:
        """Compute the sleep duration for *attempt* (0-based), respecting Retry-After."""
        # Honour Retry-After header when the provider sends it.
        resp = getattr(exc, "response", None)
        if resp is not None:
            headers = getattr(resp, "headers", {}) or {}
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra:
                try:
                    return float(ra)
                except (TypeError, ValueError):
                    pass

        base = min(self.retry_delay * (2 ** attempt), self.max_retry_delay)
        return base + random.uniform(0.0, self.retry_jitter)

    def _with_retry(self, fn, *args, **kwargs):
        """Call fn(*args, **kwargs) with exponential-backoff + jitter retry.

        On each transient failure (429, 5xx, timeout, connection error) the
        delay doubles from *retry_delay*, capped at *max_retry_delay*, with up
        to *retry_jitter* seconds of uniform noise added to avoid thundering herd.
        If the response includes a ``Retry-After`` header its value is used
        directly instead of the computed delay.
        """
        last_exc: BaseException = RuntimeError("unreachable")
        for attempt in range(self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retries or not _is_retryable(exc):
                    raise
                delay = self._retry_delay_for(attempt, exc)
                log.warning(
                    "llm_retry attempt=%d/%d delay=%.1fs agent=%s exc=%s",
                    attempt + 1, self.max_retries, delay,
                    self.current_agent, type(exc).__name__,
                )
                time.sleep(delay)
        raise last_exc  # pragma: no cover

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 16384,
        json_mode: bool = False,
    ) -> str:
        """Send messages and return the full reply as a string.

        When ``self.on_token`` is set, stream the response and call
        ``on_token(chunk)`` for each piece of text; still return the full
        concatenated string at the end.

        When ``json_mode=True``, the response is guaranteed to be valid JSON.
        Adapters that support native JSON mode (OpenAI ``response_format``,
        Gemini ``responseMimeType``) use it; others ignore the flag and rely
        on the caller's retry-with-hint logic.

        Must call ``self._record_usage(input_tokens, output_tokens)`` after
        every successful call.
        """

    def system(self, prompt: str, user: str, **kwargs) -> str:
        """One system + one user message, with cache, optional streaming, and trace."""
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

        _trace_active = self.trace is not None and self._trace_run_id is not None
        _usage_before = len(self._usage_log) if _trace_active else 0
        _t0 = time.monotonic() if _trace_active else 0.0

        # Resolve result (cache hit, streaming, or retry path).
        # Streaming retries too: after the first transient failure we disable
        # on_token for subsequent attempts to avoid sending partial duplicate
        # tokens to the progress panel.
        _stream_disabled = [False]

        def _complete_possibly_streaming(msgs, **kw):
            if _stream_disabled[0]:
                saved, self.on_token = self.on_token, None
                try:
                    return self.complete(msgs, **kw)
                finally:
                    self.on_token = saved
            try:
                return self.complete(msgs, **kw)
            except Exception as exc:
                if _is_retryable(exc):
                    _stream_disabled[0] = True
                raise

        if cache is not None:
            hit = cache.get(messages, model, validate=_is_complete_response, agent_name=agent)
            if hit is not None:
                result = hit
            else:
                result = self._with_retry(self.complete, messages, **kwargs)
                cache.set(messages, model, result, agent_name=agent)
        elif self.on_token is not None:
            result = self._with_retry(_complete_possibly_streaming, messages, **kwargs)
        else:
            result = self._with_retry(self.complete, messages, **kwargs)

        if _trace_active:
            added = self._usage_log[_usage_before:]
            self.trace.record_call(  # type: ignore[union-attr]
                run_id=self._trace_run_id,  # type: ignore[arg-type]
                agent_name=self.current_agent,
                duration_ms=(time.monotonic() - _t0) * 1000,
                input_tokens=sum(e["input_tokens"] for e in added),
                output_tokens=sum(e["output_tokens"] for e in added),
                cost_usd=sum(e["cost_usd"] for e in added),
                prompt_snippet=prompt,
                response_snippet=result,
                prompt_full=prompt,
                response_full=result,
                user_full=user,
            )

        return result

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

        from antcrew_engine.models.cache import LLMCache as _LLMCache
        if isinstance(cache, (str, os.PathLike)):
            from antcrew_engine.models.cache import FileLLMCache as _FC
            self.cache = _FC(cache)
        elif cache is not None:
            self.cache = cache
        else:
            self.cache = _LLMCache()
        return self

    def _maybe_compress(self, text: str, *, label: str = "") -> str:
        """Return *text* compressed if it exceeds context_budget_tokens, else unchanged.

        This is an opt-in helper — callers pass variable context (e.g. a large
        file) through this method before building the system prompt.  It has no
        effect unless both ``context_compressor`` and ``context_budget_tokens``
        are set on the instance.

        The method NEVER touches cached blocks or transport-level content; it is
        the caller's responsibility to decide which strings benefit from compression.
        """
        if self.context_compressor is None or self.context_budget_tokens is None:
            return text

        estimated_tokens = len(text) // 4
        if estimated_tokens <= self.context_budget_tokens:
            return text

        result = self.context_compressor.compress(
            text,
            budget_tokens=self.context_budget_tokens,
            file_path=label,
        )
        log.debug(
            "context_compressed label=%r original_tokens=%d compressed_tokens=%d method=%s",
            label,
            result.original_tokens,
            result.compressed_tokens,
            result.method,
        )
        return result.text

    def with_fallback(self, *fallbacks: "BaseLLM") -> "BaseLLM":
        """Return a FallbackLLM that tries self first, then each fallback in order.

        Example:
            llm = AnthropicModel("claude-sonnet-4-6").with_fallback(
                OpenAIModel("gpt-4o-mini"),
                GeminiModel("gemini-2.0-flash"),
            )
        """
        from antcrew_engine.models.fallback import FallbackLLM
        return FallbackLLM([self, *fallbacks])
