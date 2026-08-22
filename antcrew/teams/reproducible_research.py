"""UC8 — Reproducible Research Pipeline for AI Labs.

Wraps ResearchTeam with mandatory full-trace recording and governance anchoring
so every experiment can be cited with a deterministic experiment_id and replayed
exactly later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from antcrew.core.agent import compute_team_hash
from antcrew.core.events import new_run_id
from antcrew.teams.research_team import ResearchTeam
from antcrew.trace import TraceLog

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExperimentRecord:
    """Immutable record of one reproducible research experiment.

    ``experiment_id`` is the canonical cite key: ``<team_hash>:<run_id>``.
    It embeds both the deterministic agent configuration (team_hash) and the
    unique run identity (run_id) so reviewers can verify:

    1. The exact agent setup used (via :func:`~antcrew.compute_team_hash`).
    2. The specific run's full prompt/response trace (via TraceLog).
    """

    experiment_id: str
    run_id: str
    team_hash: str
    request: str
    cost_usd: float
    state: dict


class ReproducibleResearchPipeline:
    """UC8: ResearchTeam wrapper that enforces full-trace + governance anchoring.

    Every call to :meth:`run` records the complete prompt/response trace and
    returns an :class:`ExperimentRecord` with a deterministic ``experiment_id``
    that can be cited in research papers and replayed later.

    Usage::

        tlog = TraceLog("experiments.db", full_trace=True)
        pipeline = ReproducibleResearchPipeline(trace_log=tlog)

        exp = pipeline.run("What are the safety properties of agentic AI?")
        print(exp.experiment_id)  # cite this in your paper

        # Replay later to check for model drift
        results = pipeline.replay_experiment(exp.experiment_id)
        for r in results:
            print(r["agent_name"], "matched:", r["matched"])

    If ``trace_log`` is omitted a SQLite database is created at ``db_path``
    (default ``"experiments.db"``).  If a TraceLog is provided it must have
    ``full_trace=True`` — otherwise a ``ValueError`` is raised immediately.
    """

    def __init__(
        self,
        model: Optional["BaseLLM"] = None,
        trace_log: Optional[TraceLog] = None,
        db_path: str = "experiments.db",
        agents: Optional[dict] = None,
    ) -> None:
        if trace_log is None:
            trace_log = TraceLog(db_path, full_trace=True)
        elif not trace_log.full_trace:
            raise ValueError(
                "ReproducibleResearchPipeline requires full_trace=True on the TraceLog. "
                "Create it with: TraceLog(path, full_trace=True)"
            )
        self._trace_log = trace_log
        self._team = ResearchTeam(model=model, agents=agents, trace_log=trace_log)

    @property
    def team_hash(self) -> str:
        """Deterministic SHA-256 hash of this pipeline's agent configuration.

        Changing any agent's name, role, stage, or tools changes this value,
        making configuration drift immediately detectable.
        """
        return compute_team_hash(list(self._team._agents.values()))

    def run(self, request: str, *, thread_id: Optional[str] = None) -> ExperimentRecord:
        """Run the research pipeline and return a citable :class:`ExperimentRecord`.

        Args:
            request:   The research question or topic.
            thread_id: Optional LangGraph thread_id; one is generated when omitted.

        Returns:
            :class:`ExperimentRecord` with ``experiment_id = "<team_hash>:<run_id>"``.
        """
        _thread_id = thread_id or new_run_id()
        result = self._team.run(request, thread_id=_thread_id)
        _team_hash = self.team_hash
        run_id = result.state.get("_run_id", _thread_id)
        exp_id = f"{_team_hash}:{run_id}"
        log.debug("Experiment recorded: %s", exp_id)
        return ExperimentRecord(
            experiment_id=exp_id,
            run_id=run_id,
            team_hash=_team_hash,
            request=request,
            cost_usd=result.cost_usd,
            state=result.state,
        )

    def replay_experiment(self, experiment_id: str) -> list[dict]:
        """Replay every agent call from a prior experiment.

        Splits ``experiment_id`` on ``:`` and uses the run_id half to look up
        the stored trace.  The TraceLog must have been created with
        ``full_trace=True`` (which this class enforces).

        Args:
            experiment_id: The ``ExperimentRecord.experiment_id`` to replay.

        Returns:
            List of per-call result dicts with keys ``call_id``, ``agent_name``,
            ``original``, ``replayed``, ``matched``, ``tokens_in``,
            ``tokens_out``, ``cost_usd``, ``duration_ms``.
        """
        _, run_id = experiment_id.split(":", 1)
        return self._trace_log.replay_all(run_id, self._team.llm)
