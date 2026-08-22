"""ComparisonLLM — run two models in parallel and expose side-by-side results.

Unlike FallbackLLM (which tries models sequentially on failure), ComparisonLLM
runs *all* models concurrently and returns the primary model's answer while
recording every model's output, cost, and latency for inspection.

Usage::

    from antcrew_engine.models.comparison import ComparisonLLM
    from antcrew_engine.models.anthropic_model import AnthropicModel
    from antcrew_engine.models.ollama_model import OllamaModel

    llm = ComparisonLLM(
        primary=AnthropicModel("claude-sonnet-4-6"),
        others=[OllamaModel("llama3")],
    )
    team = DevTeam(model=llm)
    result = team.run("Build JWT auth")

    # After the run, inspect the comparison:
    for entry in llm.comparison_log():
        print(entry["model"], entry["cost_usd"], entry["duration_s"])
        print(entry["output"][:200])
"""
from __future__ import annotations

import logging
import threading
import time

from antcrew_engine.models.base import BaseLLM, Message

_log = logging.getLogger(__name__)


class ComparisonLLM(BaseLLM):
    """Composite LLM that runs the primary and all *others* concurrently.

    The **primary** model's output is always returned to the caller.
    Every model's output, cost, and wall-clock latency are recorded in
    ``_comparison_log`` for post-run analysis.

    ``current_agent`` and ``on_token`` are propagated to the primary only;
    streaming from secondary models is suppressed to avoid interleaving.

    ``get_usage_summary()`` returns the primary model's usage only (so cost
    limits apply to the primary spend, not the total).  Use
    ``full_usage_summary()`` for the aggregate across all models.
    """

    def __init__(self, primary: BaseLLM, others: list[BaseLLM]) -> None:
        if not others:
            raise ValueError("ComparisonLLM requires at least one 'others' model.")
        object.__setattr__(self, "_primary", primary)
        object.__setattr__(self, "_others", list(others))
        object.__setattr__(self, "_comparison_log_impl", [])
        object.__setattr__(self, "_lock", threading.Lock())

    # ------------------------------------------------------------------
    # Attribute propagation — only to primary
    # ------------------------------------------------------------------

    def __setattr__(self, name: str, value) -> None:
        object.__setattr__(self, name, value)
        if name in ("current_agent", "on_token", "cache", "trace", "_trace_run_id",
                    "max_cost_usd", "_cost_limit_offset"):
            object.__setattr__(self._primary, name, value)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def system(self, prompt: str, user: str, **kwargs) -> str:
        primary: BaseLLM = self._primary  # type: ignore[attr-defined]
        others: list[BaseLLM] = self._others  # type: ignore[attr-defined]
        agent = getattr(self, "current_agent", "")

        results: dict[int, dict] = {}

        def _run_model(idx: int, model: BaseLLM, is_primary: bool) -> None:
            name = getattr(model, "model", None) or type(model).__name__
            t0 = time.monotonic()
            try:
                # Suppress streaming for comparison models to avoid interleaving.
                call_kwargs = dict(kwargs)
                if not is_primary:
                    call_kwargs.pop("stream", None)
                output = model.system(prompt, user, **call_kwargs)
                duration = round(time.monotonic() - t0, 3)
                try:
                    usage = model.get_usage_summary()
                    cost = usage.get("total_cost_usd", 0.0)
                    tok_in = usage.get("total_input_tokens", 0)
                    tok_out = usage.get("total_output_tokens", 0)
                except Exception:
                    cost = tok_in = tok_out = 0
                with self._lock:  # type: ignore[attr-defined]
                    results[idx] = {
                        "model": name,
                        "agent": agent,
                        "output": output,
                        "cost_usd": cost,
                        "tokens_in": tok_in,
                        "tokens_out": tok_out,
                        "duration_s": duration,
                        "error": None,
                        "is_primary": is_primary,
                    }
            except Exception as exc:
                duration = round(time.monotonic() - t0, 3)
                _log.warning("ComparisonLLM: model %s failed — %s", name, exc)
                with self._lock:  # type: ignore[attr-defined]
                    results[idx] = {
                        "model": name,
                        "agent": agent,
                        "output": None,
                        "cost_usd": 0.0,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "duration_s": duration,
                        "error": str(exc),
                        "is_primary": is_primary,
                    }

        threads = [threading.Thread(target=_run_model, args=(0, primary, True), daemon=True)]
        for i, model in enumerate(others, start=1):
            threads.append(threading.Thread(target=_run_model, args=(i, model, False), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Persist to log (primary first, then others in order)
        for idx in sorted(results):
            with self._lock:  # type: ignore[attr-defined]
                self._comparison_log_impl.append(results[idx])  # type: ignore[attr-defined]

        primary_result = results.get(0)
        if primary_result and primary_result["error"] is not None:
            raise RuntimeError(primary_result["error"])
        if primary_result is None:
            raise RuntimeError("ComparisonLLM: primary model did not produce a result.")
        return primary_result["output"]  # type: ignore[return-value]

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        return self._primary.complete(messages, max_tokens=max_tokens, json_mode=json_mode)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Usage — primary only for cost guards; full for inspection
    # ------------------------------------------------------------------

    def get_usage_summary(self) -> dict:
        return self._primary.get_usage_summary()  # type: ignore[attr-defined]

    def full_usage_summary(self) -> dict:
        """Aggregate token/cost across all models (primary + others)."""
        all_log: list[dict] = []
        for m in [self._primary, *self._others]:  # type: ignore[attr-defined]
            all_log.extend(m._usage_log)
        if not all_log:
            return {"total_input_tokens": 0, "total_output_tokens": 0, "total_cost_usd": 0.0, "by_model": {}}
        by_model: dict[str, dict] = {}
        for e in all_log:
            m = e.get("model", "unknown")
            if m not in by_model:
                by_model[m] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
            by_model[m]["input_tokens"]  += e.get("input_tokens", 0)
            by_model[m]["output_tokens"] += e.get("output_tokens", 0)
            by_model[m]["cost_usd"]      += e.get("cost_usd", 0.0)
            by_model[m]["calls"]         += 1
        return {
            "total_input_tokens":  sum(e.get("input_tokens", 0)  for e in all_log),
            "total_output_tokens": sum(e.get("output_tokens", 0) for e in all_log),
            "total_cost_usd":      round(sum(e.get("cost_usd", 0.0) for e in all_log), 6),
            "by_model":            by_model,
        }

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def comparison_log(self) -> list[dict]:
        """Return every recorded comparison entry (all agents, all models)."""
        with self._lock:  # type: ignore[attr-defined]
            return list(self._comparison_log_impl)  # type: ignore[attr-defined]

    def comparison_summary(self) -> list[dict]:
        """Per-model aggregate: avg cost, avg latency, error rate."""
        log = self.comparison_log()
        buckets: dict[str, dict] = {}
        for e in log:
            m = e["model"]
            if m not in buckets:
                buckets[m] = {"calls": 0, "errors": 0, "total_cost": 0.0, "total_duration": 0.0}
            buckets[m]["calls"] += 1
            if e["error"]:
                buckets[m]["errors"] += 1
            buckets[m]["total_cost"]     += e["cost_usd"]
            buckets[m]["total_duration"] += e["duration_s"]
        summary = []
        for model, b in buckets.items():
            n = b["calls"]
            summary.append({
                "model":          model,
                "calls":          n,
                "error_rate":     round(b["errors"] / n, 3) if n else 0.0,
                "avg_cost_usd":   round(b["total_cost"] / n, 6) if n else 0.0,
                "avg_duration_s": round(b["total_duration"] / n, 3) if n else 0.0,
                "total_cost_usd": round(b["total_cost"], 6),
            })
        return sorted(summary, key=lambda x: x["avg_cost_usd"])
