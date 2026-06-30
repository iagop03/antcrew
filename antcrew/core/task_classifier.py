"""Task-type classifier and minimal pipeline selector.

Classifies a request into one of five task types — fix, refactor, feature,
test, docs — and returns a DevTeam configured with the minimum set of agents
and pipeline edges needed for that task.

Running a full 8-agent pipeline for a bugfix wastes 6-7 LLM calls.  This
module provides the narrowest pipeline that still produces the right output.

Usage::

    from antcrew.core.task_classifier import classify_task, MinimalPipeline
    from antcrew.models.anthropic_model import AnthropicModel

    task_type = classify_task("Fix the failing test in auth.py")
    # → TaskType.FIX

    pipeline = MinimalPipeline(AnthropicModel())
    result = pipeline.run("Fix the failing test in auth.py")
    # Runs only BackendDevAgent (+ optional QA for fix verification)

YAML config (team: minimal)::

    team: minimal
    model: claude
    # Optionally lock to a specific task type instead of auto-classifying:
    task_type: fix   # fix | refactor | feature | test | docs | auto (default)
"""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM
    from antcrew.core.run_result import RunResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    FIX      = "fix"       # bug fix, failing test, error correction
    REFACTOR = "refactor"  # restructure without changing behaviour
    FEATURE  = "feature"   # new functionality (full pipeline)
    TEST     = "test"      # write / update tests only
    DOCS     = "docs"      # documentation update only


# Pipeline definitions: minimum agents for each task type
_PIPELINE_FLOWS: dict[TaskType, list[tuple]] = {
    TaskType.FIX: [
        ("backend_dev", "qa"),
    ],
    TaskType.REFACTOR: [
        ("backend_dev", "reviewer"),
    ],
    TaskType.FEATURE: [
        ("business_analyst", "pm"),
        ("pm", "backend_dev"),
    ],
    TaskType.TEST: [
        ("qa",),   # single-node pipeline (QAAgent only)
    ],
    TaskType.DOCS: [
        ("doc_writer",),
    ],
}

_AGENTS_FOR_TYPE: dict[TaskType, list[str]] = {
    TaskType.FIX:      ["backend_dev", "qa"],
    TaskType.REFACTOR: ["backend_dev", "reviewer"],
    TaskType.FEATURE:  ["business_analyst", "pm", "backend_dev"],
    TaskType.TEST:     ["qa"],
    TaskType.DOCS:     ["doc_writer"],
}


# ---------------------------------------------------------------------------
# Rule-based classifier (fast, no LLM call)
# ---------------------------------------------------------------------------

_RULES: list[tuple[re.Pattern, TaskType]] = [
    (re.compile(
        r"\b(fix(e[sd])?|bug|broken|break|broke|fail(ing|ed)?|error|crash\w*|"
        r"exception|traceback|debug|patch)\b",
        re.I,
    ), TaskType.FIX),
    (re.compile(
        r"\b(refactor|restructure|clean.?up|rename|extract|simplify|decouple)\b",
        re.I,
    ), TaskType.REFACTOR),
    (re.compile(
        r"\b(tests?|pytest|spec|coverage|assert|mock|stub|fixture)\b",
        re.I,
    ), TaskType.TEST),
    (re.compile(
        r"\b(docs?|documentation|readme|changelog|comment|docstring)\b",
        re.I,
    ), TaskType.DOCS),
    (re.compile(
        r"\b(build|create|implement|develop|generate|add|new|feature|endpoint|api|model)\b",
        re.I,
    ), TaskType.FEATURE),
]


def classify_task(request: str) -> TaskType:
    """Classify *request* into a TaskType using fast rule matching.

    Rules are evaluated in priority order: fix → refactor → test → docs → feature.
    Defaults to FEATURE (full pipeline) when no rule matches.
    """
    for pattern, task_type in _RULES:
        if pattern.search(request):
            log.debug("task_classifier: '%s…' → %s", request[:40], task_type.value)
            return task_type
    log.debug("task_classifier: no match for '%s…' — defaulting to FEATURE", request[:40])
    return TaskType.FEATURE


# ---------------------------------------------------------------------------
# LLM-based classifier (more accurate, costs one LLM call)
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
Classify the following software development request into exactly one of these categories:

fix      — bug fix, error correction, failing test repair
refactor — restructuring code without changing behaviour
feature  — new functionality, new endpoint, new component, new module
test     — writing or updating tests only
docs     — documentation update only

Request: {request}

Respond ONLY with the single lowercase word (one of: fix, refactor, feature, test, docs).
"""


def classify_task_llm(request: str, llm: "BaseLLM") -> TaskType:
    """Classify *request* using an LLM call.  Fallback to rule-based on error."""
    try:
        from antcrew.core.agent import _strip_fences
        raw = llm.chat([
            {"role": "user", "content": _CLASSIFY_PROMPT.format(request=request)}
        ])
        label = _strip_fences(raw).strip().lower().split()[0]
        return TaskType(label)
    except Exception as exc:
        log.warning("classify_task_llm failed (%s) — falling back to rules", exc)
        return classify_task(request)


# ---------------------------------------------------------------------------
# MinimalPipeline
# ---------------------------------------------------------------------------

class MinimalPipeline:
    """DevTeam-like runner that selects the narrowest pipeline for the request.

    Avoids spinning up all 8 agents when the task is clearly a bugfix or a
    documentation update.  Falls back to the full feature pipeline when the
    task type is ambiguous.

    Args:
        model:       LLM to use for all agents.
        task_type:   Force a specific task type. ``None`` = auto-classify.
        use_llm_classifier: Use an LLM call to classify (more accurate but
                             slower).  Default False = rule-based.
        **kwargs:    Forwarded to DevTeam (runner, memory, feedback_rounds, …).
    """

    def __init__(
        self,
        model: "Optional[BaseLLM]" = None,
        *,
        task_type: Optional[TaskType | str] = None,
        use_llm_classifier: bool = False,
        **kwargs,
    ) -> None:
        from antcrew.models.anthropic_model import AnthropicModel
        self._model = model or AnthropicModel()
        self._forced_type: Optional[TaskType] = (
            TaskType(task_type) if isinstance(task_type, str) else task_type
        )
        self._use_llm = use_llm_classifier
        self._team_kwargs = kwargs

    def run(self, request: str, *, thread_id: str = "default") -> "RunResult":
        """Classify request, build the minimal team, and run."""
        task_type = self._classify(request)
        log.info("MinimalPipeline: task_type=%s for request '%s…'", task_type.value, request[:50])
        team = self._build_team(task_type)
        result = team.run(request, thread_id=thread_id)
        result.state["_task_type"] = task_type.value
        return result

    def _classify(self, request: str) -> TaskType:
        if self._forced_type is not None:
            return self._forced_type
        if self._use_llm:
            return classify_task_llm(request, self._model)
        return classify_task(request)

    def _build_team(self, task_type: TaskType):
        from antcrew.teams.dev_team import DevTeam
        from antcrew.core.supervisor import Supervisor

        agents_needed = _AGENTS_FOR_TYPE[task_type]
        flow = _PIPELINE_FLOWS[task_type]

        # For single-node pipelines (TEST, DOCS), build a trivial 1-step flow
        if len(agents_needed) == 1:
            # Supervisor needs at least one edge — use a self-loop sentinel
            # DevTeam's default flow expects at least 2 agents, so we override
            # the supervisor with a single-node graph
            supervisor = _SingleNodeSupervisor(agents_needed[0])
        else:
            supervisor = Supervisor(flow=list(flow))

        # Build agent overrides (only instantiate what's needed)
        agents = _make_agents(agents_needed, self._model)

        return DevTeam(
            model=self._model,
            agents=agents,
            supervisor=supervisor,
            **self._team_kwargs,
        )


class _SingleNodeSupervisor:
    """Minimal supervisor that runs exactly one agent and returns."""

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def build(self, agents: dict, *, checkpointer=None):
        return _SingleNodeApp(agents[self._agent_name])


class _SingleNodeApp:
    """Graph-like object that calls one agent and returns its output as state."""

    def __init__(self, agent) -> None:
        self._agent = agent

    def invoke(self, state: dict, config: dict = None) -> dict:
        result = self._agent.run(state)
        return {**state, **result}

    def get_state(self, config: dict):
        return type("S", (), {"values": {}})()


def _make_agents(names: list[str], model: "BaseLLM") -> dict:
    """Instantiate only the agents named in *names*."""
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.agents.pm import PMAgent
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.qa import QAAgent
    from antcrew.agents.reviewer import ReviewerAgent
    from antcrew.agents.doc_writer import DocWriterAgent

    registry = {
        "business_analyst": lambda: BusinessAnalystAgent(model),
        "pm":               lambda: PMAgent(model),
        "backend_dev":      lambda: BackendDevAgent(model),
        "qa":               lambda: QAAgent(model),
        "reviewer":         lambda: ReviewerAgent(model),
        "doc_writer":       lambda: DocWriterAgent(model),
    }
    return {name: registry[name]() for name in names if name in registry}
