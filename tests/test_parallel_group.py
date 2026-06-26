"""Tests for ParallelGroup concurrent execution and state merge."""
from __future__ import annotations

import threading
import time

import pytest

from antcrew.core.supervisor import ParallelGroup, Supervisor, _merge, parallel
from antcrew.core.state import TeamState
from antcrew.models.simulated import SimulatedLLM
from antcrew.teams.dev_team import DevTeam
from antcrew.teams.fullstack_team import FullStackTeam


# ---------------------------------------------------------------------------
# _merge helper
# ---------------------------------------------------------------------------

def test_merge_empty_base():
    result = _merge({}, {"code_artifacts": [1, 2]})
    assert result == {"code_artifacts": [1, 2]}


def test_merge_concatenates_lists():
    base   = {"code_artifacts": [1, 2]}
    update = {"code_artifacts": [3, 4]}
    assert _merge(base, update)["code_artifacts"] == [1, 2, 3, 4]


def test_merge_unions_dicts():
    base   = {"metadata": {"a": 1}}
    update = {"metadata": {"b": 2}}
    assert _merge(base, update)["metadata"] == {"a": 1, "b": 2}


def test_merge_last_write_for_scalars():
    base   = {"current_agent": "backend_dev"}
    update = {"current_agent": "frontend_dev"}
    assert _merge(base, update)["current_agent"] == "frontend_dev"


def test_merge_none_value_does_not_overwrite():
    base   = {"prd": "something"}
    update = {"prd": None}
    assert _merge(base, update)["prd"] == "something"


def test_merge_none_base_accepts_update():
    base   = {"prd": None}
    update = {"prd": "something"}
    assert _merge(base, update)["prd"] == "something"


# ---------------------------------------------------------------------------
# ParallelGroup construction
# ---------------------------------------------------------------------------

def test_parallel_requires_at_least_one_agent():
    with pytest.raises(ValueError, match="at least one agent"):
        ParallelGroup()


def test_parallel_helper_returns_group():
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent
    llm = SimulatedLLM()
    group = parallel(BackendDevAgent(llm), FrontendDevAgent(llm), name="coding")
    assert isinstance(group, ParallelGroup)
    assert group.name == "coding"
    assert len(group._agents) == 2


def test_parallel_group_default_name():
    from antcrew.agents.backend_dev import BackendDevAgent
    llm = SimulatedLLM()
    group = parallel(BackendDevAgent(llm))
    assert group.name == "parallel_group"


# ---------------------------------------------------------------------------
# ParallelGroup.run — state merge
# ---------------------------------------------------------------------------

class _FixedAgent:
    """Minimal agent that returns a fixed partial state."""
    approval_required = False

    def __init__(self, update: dict, delay: float = 0.0) -> None:
        self._update = update
        self._delay = delay
        self.ran = False

    def run(self, state: TeamState) -> dict:
        if self._delay:
            time.sleep(self._delay)
        self.ran = True
        return dict(self._update)


def _empty_state() -> TeamState:
    return {
        "request": "test",
        "messages": [],
        "prd": None,
        "tickets": None,
        "code_artifacts": None,
        "test_artifacts": None,
        "review": None,
        "devops_artifacts": None,
        "doc_artifacts": None,
        "research_document": None,
        "content_piece": None,
        "current_agent": "",
        "errors": [],
        "metadata": {},
    }


def test_parallel_group_runs_all_agents():
    a = _FixedAgent({"metadata": {"from_a": True}})
    b = _FixedAgent({"metadata": {"from_b": True}})
    group = ParallelGroup(a, b)
    result = group.run(_empty_state())
    assert a.ran
    assert b.ran


def test_parallel_group_merges_list_outputs():
    from antcrew.core.artifacts import CodeArtifact
    art_a = CodeArtifact(ticket_id="T-1", file_path="a.py", language="python", content="# a")
    art_b = CodeArtifact(ticket_id="T-2", file_path="b.py", language="python", content="# b")
    a = _FixedAgent({"code_artifacts": [art_a]})
    b = _FixedAgent({"code_artifacts": [art_b]})
    group = ParallelGroup(a, b)
    result = group.run(_empty_state())
    assert len(result["code_artifacts"]) == 2
    paths = {art.file_path for art in result["code_artifacts"]}
    assert paths == {"a.py", "b.py"}


def test_parallel_group_merges_metadata():
    a = _FixedAgent({"metadata": {"key_a": 1}})
    b = _FixedAgent({"metadata": {"key_b": 2}})
    group = ParallelGroup(a, b)
    result = group.run(_empty_state())
    assert result["metadata"]["key_a"] == 1
    assert result["metadata"]["key_b"] == 2


def test_parallel_group_runs_concurrently():
    """Both agents should overlap in wall-clock time."""
    SLEEP = 0.12
    a = _FixedAgent({"metadata": {"a": 1}}, delay=SLEEP)
    b = _FixedAgent({"metadata": {"b": 1}}, delay=SLEEP)
    start = time.monotonic()
    ParallelGroup(a, b).run(_empty_state())
    elapsed = time.monotonic() - start
    # Sequential would take ≥ 2*SLEEP; parallel should be well under 1.8*SLEEP
    assert elapsed < SLEEP * 1.8, f"Looks sequential: elapsed={elapsed:.3f}s"


def test_parallel_group_propagates_exceptions():
    class _BoomAgent:
        approval_required = False
        def run(self, state):
            raise RuntimeError("boom")

    group = ParallelGroup(_BoomAgent())
    with pytest.raises(RuntimeError, match="boom"):
        group.run(_empty_state())


# ---------------------------------------------------------------------------
# ParallelGroup.approval_required
# ---------------------------------------------------------------------------

def test_parallel_group_approval_required_false_by_default():
    a = _FixedAgent({})
    b = _FixedAgent({})
    group = ParallelGroup(a, b)
    assert group.approval_required is False


def test_parallel_group_approval_required_true_if_any_inner():
    class _ApprovalAgent:
        approval_required = True
        def run(self, state): return {}

    a = _FixedAgent({})
    b = _ApprovalAgent()
    group = ParallelGroup(a, b)
    assert group.approval_required is True


# ---------------------------------------------------------------------------
# Supervisor.build + ParallelGroup integration
# ---------------------------------------------------------------------------

def test_supervisor_builds_with_parallel_group():
    """Supervisor.build() should accept a ParallelGroup as a node."""
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent
    from antcrew.agents.pm import PMAgent
    from antcrew.agents.qa import QAAgent

    llm = SimulatedLLM()
    supervisor = Supervisor(flow=[
        ("pm",     "coding"),
        ("coding", "qa"),
    ])
    agents = {
        "pm":     PMAgent(llm),
        "coding": parallel(BackendDevAgent(llm), FrontendDevAgent(llm), name="coding"),
        "qa":     QAAgent(llm),
    }
    app = supervisor.build(agents)
    assert app is not None


def test_supervisor_parallel_group_end_to_end():
    """ParallelGroup node runs both inner agents; both append to messages."""
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent

    llm = SimulatedLLM()
    supervisor = Supervisor(flow=[("coding", "coding")])  # single-node loop avoided
    # Use a two-node flow: a → group
    supervisor2 = Supervisor(flow=[("coding", "done")])

    a_ran = threading.Event()
    b_ran = threading.Event()

    class _TAgent:
        approval_required = False
        def __init__(self, evt): self._evt = evt
        def run(self, state):
            self._evt.set()
            return {"metadata": {}}

    class _DoneAgent:
        approval_required = False
        def run(self, state): return {}

    group = ParallelGroup(_TAgent(a_ran), _TAgent(b_ran), name="coding")
    supervisor3 = Supervisor(flow=[("coding", "done")])
    agents = {"coding": group, "done": _DoneAgent()}
    app = supervisor3.build(agents)
    config = {"configurable": {"thread_id": "test-pg-e2e"}}
    app.invoke(_empty_state(), config=config)
    assert a_ran.is_set()
    assert b_ran.is_set()


# ---------------------------------------------------------------------------
# _unique_llms with ParallelGroup
# ---------------------------------------------------------------------------

def test_unique_llms_recurses_into_parallel_group():
    """Teams with a ParallelGroup agent should expose inner LLMs via _unique_llms."""
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent

    primary = SimulatedLLM()
    secondary = SimulatedLLM()
    group = parallel(
        BackendDevAgent(primary),
        FrontendDevAgent(secondary),
        name="coding",
    )

    team = DevTeam(model=primary, agents={"backend_dev": group})
    llms = team._unique_llms()
    assert secondary in llms


def test_unique_llms_deduplicates_across_parallel_group():
    """Same LLM instance in two inner agents counts once."""
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent

    shared = SimulatedLLM()
    group = parallel(BackendDevAgent(shared), FrontendDevAgent(shared), name="coding")
    team = DevTeam(model=shared, agents={"backend_dev": group})
    llms = team._unique_llms()
    assert llms.count(shared) == 1
