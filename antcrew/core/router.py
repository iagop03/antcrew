"""Auto-routing — classify a request and dispatch to the right team/agent.

Avoids running a full multi-agent pipeline for simple requests that can be
answered in a single LLM call.

Usage — Python::

    from antcrew import Router, DirectAgent, DevTeam, LLMClassifier

    llm = AnthropicModel()
    router = Router(
        classifier=LLMClassifier(llm, routes={
            "simple":  "Factual question or short explanation (no code needed)",
            "complex": "Software development task, code generation, system design",
        }),
        routes={
            "simple":  DirectAgent(llm, system_prompt="Answer concisely."),
            "complex": DevTeam(llm),
        },
        default="complex",
    )
    result = router.run("What is JWT?")          # → simple route, 1 LLM call
    result = router.run("Build JWT auth module") # → complex route, full pipeline
    print(result.state["_route"])                # "simple" or "complex"

Usage — rule-based (no LLM classification call)::

    from antcrew import Router, RuleClassifier

    router = Router(
        classifier=RuleClassifier(rules=[
            (r"\\b(what|who|when|where|why|how|explain|define)\\b", "simple"),
            (r"\\b(build|create|implement|develop|generate|add)\\b", "complex"),
        ], default="complex"),
        routes={...},
        default="complex",
    )

YAML config::

    team: auto
    model: claude
    simple_prompt: "You are a helpful assistant. Answer concisely."
    complex_team: dev          # dev | fullstack | custom | feature
    # Optional label descriptions shown to the classifier LLM:
    route_descriptions:
      simple: "Factual questions, quick explanations, no code needed"
      complex: "Code generation, software development, system design"

    team: routed               # full control
    model: claude
    classifier: llm            # llm | rule
    routes:
      simple:
        team: direct
        system_prompt: "Answer the question concisely."
      complex:
        team: dev
    default_route: complex
"""
from __future__ import annotations

import abc
import logging
import re
from typing import TYPE_CHECKING, Any, Optional

from antcrew.core.events import bus, new_run_id

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM
    from antcrew.core.run_result import RunResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classifier base
# ---------------------------------------------------------------------------

class RouteClassifier(abc.ABC):
    """Decides which route label applies to a request."""

    @abc.abstractmethod
    def classify(self, request: str) -> str:
        """Return the route label for *request*."""


# ---------------------------------------------------------------------------
# LLMClassifier
# ---------------------------------------------------------------------------

_LLM_CLASSIFY_SYSTEM = """\
You are a request router. Classify the incoming request into exactly one of \
the following categories:

{labels_block}

Rules:
- Respond with ONLY the category label — nothing else.
- No explanation, no punctuation, no extra words.
- When in doubt, use the last category.
"""


class LLMClassifier(RouteClassifier):
    """Uses the LLM to classify a request into one of the declared routes.

    Args:
        llm:     LLM used for classification (can be a cheaper/faster model).
        routes:  Mapping of label → description shown to the LLM.
                 E.g. ``{"simple": "Quick factual questions", "complex": "..."}``.
        default: Fallback label when the LLM response cannot be parsed.
    """

    def __init__(
        self,
        llm: "BaseLLM",
        routes: dict[str, str],
        *,
        default: Optional[str] = None,
    ) -> None:
        if not routes:
            raise ValueError("LLMClassifier requires at least one route.")
        self._llm = llm
        self._routes = routes
        self._labels = list(routes)
        self._default = default or self._labels[-1]

        labels_block = "\n".join(
            f'- "{label}": {desc}' for label, desc in routes.items()
        )
        self._system = _LLM_CLASSIFY_SYSTEM.format(labels_block=labels_block)

    def classify(self, request: str) -> str:
        try:
            raw = self._llm.system(self._system, request).strip().lower()
            for label in self._labels:
                if label.lower() in raw:
                    log.debug("router_classify label=%s request=%r", label, request[:60])
                    return label
        except Exception as exc:
            log.warning("router_classify_error %s — using default=%s", exc, self._default)
        log.debug("router_classify fallback default=%s", self._default)
        return self._default


# ---------------------------------------------------------------------------
# RuleClassifier
# ---------------------------------------------------------------------------

class RuleClassifier(RouteClassifier):
    """Classifies requests using regex rules — no LLM call, instant.

    Rules are evaluated in order; the first match wins.

    Args:
        rules:   List of ``(pattern, label)`` tuples.  Patterns are
                 compiled with ``re.IGNORECASE``.
        default: Label returned when no rule matches.
    """

    def __init__(
        self,
        rules: list[tuple[str, str]],
        *,
        default: str = "complex",
    ) -> None:
        self._rules = [(re.compile(p, re.IGNORECASE), label) for p, label in rules]
        self._default = default

    def classify(self, request: str) -> str:
        for pattern, label in self._rules:
            if pattern.search(request):
                log.debug("router_rule_match label=%s pattern=%r", label, pattern.pattern)
                return label
        return self._default


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class Router:
    """Dispatches requests to the appropriate team/agent based on classification.

    Has the same ``run(request) -> RunResult`` interface as all other teams so
    it composes transparently wherever a team is expected.

    Injects ``_route`` (the chosen label) into the returned state so callers
    can inspect the routing decision.

    When a :class:`~antcrew.trace.TraceLog` is attached via *trace_log*, the
    routing decision is recorded as an ``agent_call`` row so it appears in
    ``antcrew trace`` output.

    Args:
        classifier: A :class:`RouteClassifier` that maps requests to labels.
        routes:     Mapping of label → team/agent with ``run(request) -> RunResult``.
        default:    Fallback label when the classifier returns an unknown label.
        trace_log:  Optional :class:`~antcrew.trace.TraceLog` for recording the
                    routing decision alongside the dispatched team's spans.
    """

    def __init__(
        self,
        *,
        classifier: RouteClassifier,
        routes: dict[str, Any],
        default: str,
        trace_log: Any = None,
    ) -> None:
        if not routes:
            raise ValueError("Router requires at least one route.")
        if default not in routes:
            raise ValueError(
                f"Default route '{default}' not in routes: {sorted(routes)}"
            )
        self._classifier = classifier
        self._routes = routes
        self._default = default
        self._trace_log = trace_log

    def run(self, request: str, *, thread_id: str = "default") -> "RunResult":
        import time as _time

        t0 = _time.monotonic()
        _run_id = new_run_id()
        _trace_run_id: Optional[str] = None

        if self._trace_log is not None:
            _trace_run_id = self._trace_log.begin_run(
                thread_id=thread_id,
                request=request,
                team=type(self).__name__,
            )

        try:
            label = self._classifier.classify(request)
            handler = self._routes.get(label)
            if handler is None:
                log.warning("router unknown label=%r using default=%s", label, self._default)
                label = self._default
                handler = self._routes[self._default]

            log.info("router dispatching label=%s", label)
            bus.emit("router.dispatch", {"label": label, "request": request},
                     run_id=_run_id, thread_id=thread_id)
            result = handler.run(request)
            result.state["_route"] = label

            if self._trace_log is not None and _trace_run_id:
                duration_ms = (_time.monotonic() - t0) * 1000
                self._trace_log.record_call(
                    run_id=_trace_run_id,
                    agent_name="router",
                    duration_ms=duration_ms,
                    prompt_snippet=request[:200],
                    response_snippet=f"→ {label}",
                )
                cost = result.state.get("_cost_usd") or 0.0
                self._trace_log.end_run(_trace_run_id, cost_usd=cost, status="done")

            return result

        except Exception:
            if self._trace_log is not None and _trace_run_id:
                self._trace_log.end_run(_trace_run_id, status="error")
            raise
