"""FallbackLLM â€” try models in order, falling back on any error.

When the primary model fails (rate-limit, API outage, quota exceeded), the
next model in the chain is tried automatically.  Each model's own retry logic
runs in full before the fallback is triggered.

Usage:
    from antcrew_engine.models.fallback import FallbackLLM
    from antcrew_engine.models.anthropic_model import AnthropicModel
    from antcrew_engine.models.openai_model import OpenAIModel
    from antcrew_engine.models.gemini_model import GeminiModel

    llm = FallbackLLM([
        AnthropicModel("claude-sonnet-4-6"),
        OpenAIModel("gpt-4o-mini"),
        GeminiModel("gemini-2.0-flash"),
    ])
    team = DevTeam(model=llm)

Or using the fluent helper on any model:

    llm = AnthropicModel("claude-sonnet-4-6").with_fallback(
        OpenAIModel("gpt-4o-mini"),
        GeminiModel("gemini-2.0-flash"),
    )
"""
from __future__ import annotations

import logging

from antcrew_engine.models.base import BaseLLM, Message

_log = logging.getLogger(__name__)


class FallbackLLM(BaseLLM):
    """Composite LLM that tries each model in order, falling back on error.

    - Each underlying model runs its own retry logic before failing.
    - ``current_agent`` and ``on_token`` set on FallbackLLM propagate
      instantly to every model in the chain.
    - ``get_usage_summary()`` aggregates tokens from all models that
      were actually called.
    - ``fallback_events()`` returns an audit log of every fallback that
      occurred, useful for alerting or debugging.
    """

    def __init__(self, models: list[BaseLLM]) -> None:
        if not models:
            raise ValueError("FallbackLLM requires at least one model.")
        # Use object.__setattr__ to avoid triggering our own __setattr__ hook
        # before _models is populated.
        object.__setattr__(self, "_models", list(models))
        object.__setattr__(self, "_fallback_events", [])

    # ------------------------------------------------------------------
    # Attribute propagation
    # ------------------------------------------------------------------

    def __setattr__(self, name: str, value) -> None:
        object.__setattr__(self, name, value)
        # Propagate runtime attributes to every model in the chain so that
        # per-model BaseLLM.system() hooks (streaming, tracing) work correctly.
        if name in ("current_agent", "on_token", "cache", "trace", "_trace_run_id"):
            for m in getattr(self, "_models", []):
                object.__setattr__(m, name, value)

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def system(self, prompt: str, user: str, **kwargs) -> str:
        """Try each model's system() in order; fall back on any exception.

        Overrides BaseLLM.system() so that each model's own retry logic
        (built into BaseLLM._with_retry) runs before the fallback triggers.
        Cost guard uses the aggregate get_usage_summary() so the limit
        applies to total spend across all models in the chain.
        """
        if self.max_cost_usd is not None:
            spent = self.get_usage_summary()["total_cost_usd"] - self._cost_limit_offset
            if spent >= self.max_cost_usd:
                from antcrew.core.exceptions import CostLimitExceeded
                raise CostLimitExceeded(spent, self.max_cost_usd)

        last_exc: BaseException = RuntimeError("No models in chain.")
        models = self._models  # type: ignore[attr-defined]

        for i, model in enumerate(models):
            try:
                return model.system(prompt, user, **kwargs)
            except Exception as exc:
                last_exc = exc
                if i < len(models) - 1:
                    next_name = type(models[i + 1]).__name__
                    _log.warning(
                        "FallbackLLM: %s failed (%s), falling back to %s",
                        type(model).__name__, exc, next_name,
                    )
                    # type: ignore[attr-defined]
                    self._fallback_events.append({  # type: ignore[attr-defined]
                        "failed_model": type(model).__name__,
                        "next_model":   next_name,
                        "error":        str(exc),
                        "agent":        getattr(self, "current_agent", ""),
                    })

        raise last_exc

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        """Try each model's complete() in order (no per-model retries here).

        In normal operation BaseAgent calls llm.system(), not complete() directly.
        This method exists to satisfy the BaseLLM ABC.
        """
        last_exc: BaseException = RuntimeError("No models in chain.")
        for model in self._models:  # type: ignore[attr-defined]
            try:
                return model.complete(messages, max_tokens=max_tokens, json_mode=json_mode)
            except Exception as exc:
                last_exc = exc
        raise last_exc

    # ------------------------------------------------------------------
    # Usage tracking â€” aggregate across all models
    # ------------------------------------------------------------------

    def get_usage_summary(self) -> dict:
        """Aggregate token usage from every model in the chain."""
        all_log: list[dict] = []
        for m in self._models:  # type: ignore[attr-defined]
            all_log.extend(m._usage_log)

        if not all_log:
            return {
                "total_input_tokens":  0,
                "total_output_tokens": 0,
                "total_cost_usd":      0.0,
                "by_agent":            [],
            }
        return {
            "total_input_tokens":  sum(e["input_tokens"]  for e in all_log),
            "total_output_tokens": sum(e["output_tokens"] for e in all_log),
            "total_cost_usd":      round(sum(e["cost_usd"] for e in all_log), 6),
            "by_agent":            all_log,
        }

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def fallback_events(self) -> list[dict]:
        """Return an audit log of every fallback that occurred.

        Each entry: {"failed_model": str, "next_model": str, "error": str, "agent": str}
        """
        return list(self._fallback_events)  # type: ignore[attr-defined]
