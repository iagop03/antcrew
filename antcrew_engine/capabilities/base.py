"""BaseExecutor: LLM-aware base class for capability implementations.

The base class handles:
  - LLM injection (provider-agnostic via antcrew.models.BaseLLM)
  - Execution timing (written into CapabilityResult.execution_time)
  - Error capture (exceptions → CapabilityResult.errors, never re-raised)
  - Python syntax gate: any SOURCE artifact with a SyntaxError is rejected
    (EMPTY_DELTA + errors) so the EngineLoop retries within limits instead of
    committing broken code to the store.

The public Executor Protocol never leaks model names or provider details.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from antcrew_engine.engine import EMPTY_DELTA, CapabilityResult

_MAX_RETRIES      = 3
_RETRY_BASE_S     = 1.0   # first retry after 1 s, then 2 s, then 4 s
_MAX_JSON_RETRIES = 2     # extra attempts when LLM produces malformed JSON


def _is_transient_error(exc: Exception) -> bool:
    """Return True for errors that are safe to retry (rate-limits, overload, timeouts)."""
    name = type(exc).__name__.lower()
    msg  = str(exc).lower()
    _TRANSIENT_NAMES = ("ratelimit", "overload", "throttl", "timeout",
                        "connectionerror", "serviceunavaila", "toomanyrequests")
    _TRANSIENT_MSGS  = ("rate limit", "overloaded", "too many requests", "timeout",
                        "service unavailable", "529", "503", "504")
    return (
        any(k in name for k in _TRANSIENT_NAMES)
        or any(k in msg  for k in _TRANSIENT_MSGS)
    )

if TYPE_CHECKING:
    from antcrew_engine.engine import ArtifactStore, Goal
    from antcrew_engine.models.base import BaseLLM


class BaseExecutor:
    """Convenience base for LLM-backed capability executors.

    Subclasses must define:
        descriptor: CapabilityDescriptor          (class attribute)
        _run(store, goal) -> CapabilityResult     (override, may raise)

    Subclasses that don't need an LLM (e.g. TestRunner) may leave
    llm=None and must not call _call().

    The EngineLoop injects _event_log before each execute() call so that
    streaming tokens are forwarded as CapabilityProgress events.
    """

    def __init__(self, llm: "Optional[BaseLLM]" = None) -> None:
        self._llm = llm
        self._event_log = None  # injected by EngineLoop; enables streaming

    def _call(self, system: str, user: str) -> str:
        """Call the injected LLM with a system + user prompt pair.

        When _event_log is set, each streaming chunk is emitted as a
        CapabilityProgress event so the platform can stream it to the UI.
        """
        if self._llm is None:
            raise RuntimeError(
                f"{type(self).__name__} requires an LLM but none was injected. "
                "Pass llm=... to the constructor."
            )
        event_log = self._event_log
        if event_log is not None:
            from antcrew_engine.engine.events import CapabilityProgress
            cap_name = self.descriptor.name

            def _on_token(chunk: str) -> None:
                event_log.emit(CapabilityProgress(capability_name=cap_name, chunk=chunk))

            self._llm.on_token = _on_token

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
            try:
                return self._llm.system(system, user)
            except Exception as exc:
                if not _is_transient_error(exc):
                    raise
                last_exc = exc
            finally:
                if event_log is not None:
                    self._llm.on_token = None

        raise last_exc  # type: ignore[misc]

    def _call_json(self, system: str, user: str) -> str:
        """Like _call(), but retries when the LLM response is not parseable JSON.

        On each parse failure, re-calls with a correction note appended so the
        LLM understands what went wrong. Returns the last raw string regardless;
        actual parsing is left to the caller.
        """
        from ._utils import parse_json
        raw = self._call(system, user)
        for attempt in range(_MAX_JSON_RETRIES):
            try:
                parse_json(raw)
                return raw
            except Exception:
                raw = self._call(
                    system,
                    user + (
                        f"\n\n[Your previous response was not valid JSON (attempt "
                        f"{attempt + 1}/{_MAX_JSON_RETRIES}). "
                        "Output ONLY the JSON object or array — no markdown fences, no prose.]"
                    ),
                )
        return raw

    def _run(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        raise NotImplementedError(f"{type(self).__name__}._run() not implemented")

    def execute(self, store: "ArtifactStore", goal: "Goal") -> CapabilityResult:
        t0 = time.monotonic()
        _before = self._llm.get_usage_summary() if self._llm else {}
        cost_before        = _before.get("total_cost_usd", 0.0)
        cache_read_before  = _before.get("total_cache_read_tokens", 0)
        cache_write_before = _before.get("total_cache_write_tokens", 0)

        try:
            result = self._run(store, goal)
            elapsed = time.monotonic() - t0

            if self._llm:
                _after = self._llm.get_usage_summary()
                cost_usd    = round(_after["total_cost_usd"] - cost_before, 6)
                cache_read  = _after.get("total_cache_read_tokens",  0) - cache_read_before
                cache_write = _after.get("total_cache_write_tokens", 0) - cache_write_before
            else:
                cost_usd = cache_read = cache_write = 0

            clean_delta, syntax_errors = _filter_python_delta(result.delta)
            if syntax_errors:
                return CapabilityResult(
                    delta              = clean_delta,
                    errors             = result.errors + syntax_errors,
                    execution_time     = elapsed,
                    cost_usd           = cost_usd,
                    cache_read_tokens  = cache_read,
                    cache_write_tokens = cache_write,
                )

            result.execution_time     = elapsed
            result.cost_usd           = cost_usd
            result.cache_read_tokens  = cache_read
            result.cache_write_tokens = cache_write
            return result
        except Exception as exc:
            return CapabilityResult(
                delta=EMPTY_DELTA,
                errors=[f"{type(exc).__name__}: {exc}"],
                execution_time=time.monotonic() - t0,
            )


def _filter_python_delta(delta) -> tuple[object, list[str]]:
    """Check .py SOURCE artifacts for syntax errors.

    Returns (filtered_delta, errors). Files with syntax errors are removed
    from the delta so valid files are still committed to the store.
    If no errors, returns the original delta unchanged.
    """
    from antcrew_engine.engine import ArtifactDelta, ArtifactKind

    errors:  list[str] = []
    bad_ids: set       = set()

    for art in (*delta.created, *delta.modified):
        if art.kind != ArtifactKind.SOURCE:
            continue
        if not isinstance(art.content, str):
            continue
        fp = art.metadata.get("file_path", str(art.id))
        if not fp.endswith(".py"):
            continue
        try:
            compile(art.content, fp, "exec")
        except SyntaxError as exc:
            errors.append(f"SyntaxError in {fp}: {exc}")
            bad_ids.add(art.id)

    if not errors:
        return delta, []

    clean_delta = ArtifactDelta(
        created  = tuple(a for a in delta.created  if a.id not in bad_ids),
        modified = tuple(a for a in delta.modified if a.id not in bad_ids),
        deleted  = delta.deleted,
        renamed  = delta.renamed,
    )
    return clean_delta, errors
