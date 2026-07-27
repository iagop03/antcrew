"""Integration tests for the EngineLoop observe→decide→dispatch loop.

These tests use stub capabilities (no real LLM) to verify:
- dispatch order respects cost and needs-filtering
- retry_limits block a stagnant capability
- total_limits cap lifetime dispatches
- cancellation via stop_event raises CANCELLED
- Python syntax gate in BaseExecutor rejects bad source artifacts
"""
from __future__ import annotations

import threading

import pytest
from antcrew_engine.capabilities.base import BaseExecutor, _filter_python_delta

from antcrew.engine import (
    EMPTY_DELTA,
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityResult,
    Condition,
    ConditionId,
    Constraints,
    DesiredProjectState,
    EngineLoop,
    EventLog,
    Goal,
    MemoryStore,
)
from antcrew.engine.operator import EngineLoopError
from antcrew.engine.validator import Validator, ValidatorResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _goal(*condition_ids: str) -> Goal:
    conds = frozenset(Condition(ConditionId(c), c) for c in condition_ids)
    return Goal(
        description="test goal",
        desired_state=DesiredProjectState(conds),
        constraints=Constraints(),
    )


def _descriptor(
    name: str,
    needs: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
    cost: float = 1.0,
    tags: tuple[str, ...] = (),
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name        = name,
        description = name,
        needs       = frozenset(ConditionId(n) for n in needs),
        produces    = frozenset(ConditionId(p) for p in produces),
        emits       = frozenset(),
        cost        = cost,
        tags        = frozenset(tags),
    )


class _ArtifactValidator(Validator):
    """Satisfied when the store contains an artifact with the given id."""

    def __init__(self, artifact_id: str, condition_id: str) -> None:
        self._art_id  = ArtifactId(artifact_id)
        self._cond_id = ConditionId(condition_id)

    @property
    def relevant_artifacts(self) -> frozenset[ArtifactId]:
        return frozenset([self._art_id])

    @property
    def global_scope(self) -> bool:
        return False

    def validate(self, store) -> ValidatorResult:
        art = store.read(self._art_id)
        return ValidatorResult(
            condition_id = self._cond_id,
            satisfied    = art is not None,
        )


class _Stub(BaseExecutor):
    """Capability stub that writes one artifact on each successful call.

    Parameters:
        descriptor   — CapabilityDescriptor
        artifact_id  — id of the artifact to create on each run (None → EMPTY_DELTA)
        fail_times   — produce EMPTY_DELTA for the first N calls (then succeed)
    """

    def __init__(
        self,
        descriptor: CapabilityDescriptor,
        artifact_id: str | None = None,
        *,
        fail_times: int = 0,
    ) -> None:
        super().__init__(llm=None)
        self.descriptor  = descriptor
        self._art_id     = ArtifactId(artifact_id) if artifact_id else None
        self._fail_times = fail_times
        self.call_count  = 0

    def _run(self, store, goal) -> CapabilityResult:
        self.call_count += 1
        if self.call_count <= self._fail_times:
            return CapabilityResult(delta=EMPTY_DELTA)
        if self._art_id is None:
            return CapabilityResult(delta=EMPTY_DELTA)
        art = Artifact(id=self._art_id, kind=ArtifactKind.GENERIC, content={})
        return CapabilityResult(delta=ArtifactDelta(created=(art,)))


def _make_operator(
    registry: CapabilityRegistry,
    validators: list,
    *,
    retry_limits: dict | None = None,
    total_limits: dict | None = None,
    stop_event=None,
    max_iterations: int = 30,
) -> EngineLoop:
    return EngineLoop(
        registry, validators, EventLog(),
        max_iterations=max_iterations,
        retry_limits=retry_limits or {},
        total_limits=total_limits or {},
        stop_event=stop_event,
    )


# ---------------------------------------------------------------------------
# Tests: basic dispatch
# ---------------------------------------------------------------------------

def test_single_capability_satisfies_goal():
    """A single capability that produces the goal condition should complete in 1 iteration."""
    cap = _Stub(_descriptor("maker", produces=("done",)), artifact_id="result")
    reg = CapabilityRegistry()
    reg.register(cap)
    validators = [_ArtifactValidator("result", "done")]

    op = _make_operator(reg, validators)
    store = MemoryStore()
    op.run(store, _goal("done"))

    assert cap.call_count == 1
    assert store.read(ArtifactId("result")) is not None


def test_dispatch_order_by_cost():
    """When two capabilities both address the same gap, cheaper one runs first."""
    call_order: list[str] = []

    class _OrderedStub(_Stub):
        def _run(self, store, goal):
            call_order.append(self.descriptor.name)
            return super()._run(store, goal)

    cheap = _OrderedStub(
        _descriptor("cheap", produces=("done",), cost=0.5),
        artifact_id="result",
    )
    expensive = _OrderedStub(
        _descriptor("expensive", produces=("done",), cost=2.0),
        artifact_id="result",
    )
    reg = CapabilityRegistry()
    reg.register(expensive)  # register in wrong order — cost should still win
    reg.register(cheap)

    validators = [_ArtifactValidator("result", "done")]
    op = _make_operator(reg, validators)
    op.run(MemoryStore(), _goal("done"))

    assert call_order[0] == "cheap"
    assert expensive.call_count == 0


def test_needs_filtering_blocks_premature_dispatch():
    """Capability B (needs='a_done') must not run before A satisfies 'a_done'."""
    a = _Stub(_descriptor("a", produces=("a_done",)), artifact_id="art_a")
    b = _Stub(
        _descriptor("b", needs=("a_done",), produces=("b_done",)),
        artifact_id="art_b",
    )
    reg = CapabilityRegistry()
    reg.register(b)
    reg.register(a)

    validators = [
        _ArtifactValidator("art_a", "a_done"),
        _ArtifactValidator("art_b", "b_done"),
    ]
    op = _make_operator(reg, validators)
    op.run(MemoryStore(), _goal("a_done", "b_done"))

    assert a.call_count == 1
    assert b.call_count == 1


# ---------------------------------------------------------------------------
# Tests: retry_limits
# ---------------------------------------------------------------------------

def test_retry_limits_block_stagnant_capability():
    """A capability that repeatedly produces EMPTY_DELTA should be blocked by retry_limits."""
    bad = _Stub(_descriptor("bad", produces=("done",)), artifact_id=None)
    reg = CapabilityRegistry()
    reg.register(bad)

    validators = [_ArtifactValidator("art_done", "done")]
    op = _make_operator(reg, validators, retry_limits={"bad": 1})

    with pytest.raises(EngineLoopError) as exc_info:
        op.run(MemoryStore(), _goal("done"))

    # After retry_limit=1 consecutive stagnant runs, no eligible candidates → STUCK
    assert exc_info.value.kind == EngineLoopError.Kind.STUCK


def test_retry_limit_resets_after_progress():
    """retry_count resets when any new condition is satisfied."""
    # "flaky" fails twice, then succeeds
    flaky = _Stub(_descriptor("flaky", produces=("done",)), artifact_id="result", fail_times=2)
    reg = CapabilityRegistry()
    reg.register(flaky)

    validators = [_ArtifactValidator("result", "done")]
    # retry_limit=3 allows 3 consecutive empty deltas — flaky fails 2, then succeeds
    op = _make_operator(reg, validators, retry_limits={"flaky": 3})
    op.run(MemoryStore(), _goal("done"))

    assert flaky.call_count == 3


# ---------------------------------------------------------------------------
# Tests: total_limits
# ---------------------------------------------------------------------------

def test_total_limits_cap_lifetime_dispatches():
    """A capability should not be dispatched more than total_limit times."""
    greedy = _Stub(_descriptor("greedy", produces=("done",)), artifact_id=None)
    reg = CapabilityRegistry()
    reg.register(greedy)

    validators = [_ArtifactValidator("art_done", "done")]
    op = _make_operator(reg, validators, total_limits={"greedy": 2}, max_iterations=20)

    with pytest.raises(EngineLoopError) as exc_info:
        op.run(MemoryStore(), _goal("done"))

    assert greedy.call_count == 2  # dispatched exactly 2 times
    assert exc_info.value.kind in (EngineLoopError.Kind.STUCK, EngineLoopError.Kind.NO_PROGRESS)


# ---------------------------------------------------------------------------
# Tests: cancellation
# ---------------------------------------------------------------------------

def test_cancellation_via_stop_event():
    """Setting stop_event before the loop starts should raise CANCELLED immediately."""
    stop = threading.Event()
    stop.set()  # pre-cancelled

    cap = _Stub(_descriptor("cap", produces=("done",)), artifact_id="result")
    reg = CapabilityRegistry()
    reg.register(cap)

    validators = [_ArtifactValidator("result", "done")]
    op = _make_operator(reg, validators, stop_event=stop)

    with pytest.raises(EngineLoopError) as exc_info:
        op.run(MemoryStore(), _goal("done"))

    assert exc_info.value.kind == EngineLoopError.Kind.CANCELLED
    assert cap.call_count == 0  # never ran


def test_cancellation_mid_run():
    """stop_event set during the run should cancel on the next iteration."""
    stop = threading.Event()
    call_count = 0

    class _SlowCap(_Stub):
        def _run(self, store, goal):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                stop.set()  # cancel after second dispatch
            return CapabilityResult(delta=EMPTY_DELTA)

    cap = _SlowCap(_descriptor("slow", produces=("done",)), artifact_id=None)
    reg = CapabilityRegistry()
    reg.register(cap)

    validators = [_ArtifactValidator("result", "done")]
    op = _make_operator(reg, validators, stop_event=stop, max_iterations=20)

    with pytest.raises(EngineLoopError) as exc_info:
        op.run(MemoryStore(), _goal("done"))

    assert exc_info.value.kind == EngineLoopError.Kind.CANCELLED


# ---------------------------------------------------------------------------
# Tests: Python syntax gate
# ---------------------------------------------------------------------------

def test_syntax_gate_rejects_bad_source_artifact():
    """_filter_python_delta returns errors and removes bad artifacts from the delta."""
    bad_art = Artifact(
        id       = ArtifactId("src_bad"),
        kind     = ArtifactKind.SOURCE,
        content  = "def broken(\n  pass",  # syntax error
        metadata = {"file_path": "bad.py"},
    )
    delta = ArtifactDelta(created=(bad_art,))
    clean_delta, errors = _filter_python_delta(delta)
    assert errors
    assert "bad.py" in errors[0]
    assert len(clean_delta.created) == 0  # bad file removed


def test_syntax_gate_accepts_valid_source():
    """_filter_python_delta returns no errors for syntactically correct Python."""
    good_art = Artifact(
        id       = ArtifactId("src_good"),
        kind     = ArtifactKind.SOURCE,
        content  = "def hello():\n    return 'world'\n",
        metadata = {"file_path": "good.py"},
    )
    delta = ArtifactDelta(created=(good_art,))
    clean_delta, errors = _filter_python_delta(delta)
    assert errors == []
    assert len(clean_delta.created) == 1  # valid file preserved


def test_syntax_gate_ignores_non_python_files():
    """_filter_python_delta skips SOURCE artifacts without a .py extension."""
    art = Artifact(
        id       = ArtifactId("readme"),
        kind     = ArtifactKind.SOURCE,
        content  = "not python !!!",
        metadata = {"file_path": "README.md"},
    )
    delta = ArtifactDelta(created=(art,))
    clean_delta, errors = _filter_python_delta(delta)
    assert errors == []
    assert len(clean_delta.created) == 1  # non-.py preserved unchanged


def test_syntax_gate_blocks_bad_capability_output():
    """BaseExecutor.execute() returns EMPTY_DELTA if _run() produces a bad SOURCE."""

    class _BadCodeGen(_Stub):
        def _run(self, store, goal):
            bad = Artifact(
                id       = ArtifactId("broken"),
                kind     = ArtifactKind.SOURCE,
                content  = "class Foo\n  pass",  # missing colon
                metadata = {"file_path": "broken.py"},
            )
            return CapabilityResult(delta=ArtifactDelta(created=(bad,)))

    cap = _BadCodeGen(_descriptor("bad_gen", produces=("done",)))
    result = cap.execute(MemoryStore(), _goal("done"))

    assert result.delta.is_empty()
    assert any("SyntaxError" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: budget limit
# ---------------------------------------------------------------------------

def test_budget_exceeded_raises():
    """EngineLoop should raise BUDGET_EXCEEDED when accumulated cost surpasses max_cost_usd."""

    class _CostyCap(_Stub):
        def execute(self, store, goal):
            result = super().execute(store, goal)
            result.cost_usd = 1.0  # simulate $1 per dispatch
            return result

    cap = _CostyCap(_descriptor("pricey", produces=("done",)), artifact_id=None)
    reg = CapabilityRegistry()
    reg.register(cap)

    validators = [_ArtifactValidator("art_done", "done")]
    op = _make_operator(reg, validators, max_iterations=20)
    op._max_cost_usd = 2.5  # budget = $2.50

    with pytest.raises(EngineLoopError) as exc_info:
        op.run(MemoryStore(), _goal("done"))

    assert exc_info.value.kind == EngineLoopError.Kind.BUDGET_EXCEEDED
    assert cap.call_count <= 3  # must stop after 3rd dispatch ($3 > $2.50)


# ---------------------------------------------------------------------------
# Tests: store filesystem_path API
# ---------------------------------------------------------------------------

def test_memory_store_filesystem_path_is_none():
    assert MemoryStore().filesystem_path() is None


def test_filesystem_store_filesystem_path_returns_root(tmp_path):
    from antcrew.engine import FilesystemStore
    store = FilesystemStore(tmp_path)
    assert store.filesystem_path() == tmp_path


# ---------------------------------------------------------------------------
# Tests: no_progress escape
# ---------------------------------------------------------------------------

def test_no_progress_raises_after_limit():
    """EngineLoop should raise NO_PROGRESS after 3 consecutive empty-delta runs."""
    cap = _Stub(_descriptor("always_empty", produces=("done",)), artifact_id=None)
    reg = CapabilityRegistry()
    reg.register(cap)

    validators = [_ArtifactValidator("art_done", "done")]
    op = _make_operator(reg, validators, max_iterations=20)

    with pytest.raises(EngineLoopError) as exc_info:
        op.run(MemoryStore(), _goal("done"))

    assert exc_info.value.kind == EngineLoopError.Kind.NO_PROGRESS
    assert cap.call_count == 3  # exactly _NO_PROGRESS_LIMIT times


# ---------------------------------------------------------------------------
# Tests: multi-step pipeline ordering
# ---------------------------------------------------------------------------

def test_multi_step_pipeline_ordering():
    """Verify that a 3-step chain runs in the correct order (A→B→C)."""
    order: list[str] = []

    class _Tracked(_Stub):
        def _run(self, store, goal):
            order.append(self.descriptor.name)
            return super()._run(store, goal)

    a = _Tracked(_descriptor("step_a", produces=("a_done",)), artifact_id="art_a")
    b = _Tracked(
        _descriptor("step_b", needs=("a_done",), produces=("b_done",), cost=1.0),
        artifact_id="art_b",
    )
    c = _Tracked(
        _descriptor("step_c", needs=("a_done", "b_done"), produces=("c_done",), cost=1.0),
        artifact_id="art_c",
    )

    reg = CapabilityRegistry()
    for cap in [c, b, a]:  # register in reverse order
        reg.register(cap)

    validators = [
        _ArtifactValidator("art_a", "a_done"),
        _ArtifactValidator("art_b", "b_done"),
        _ArtifactValidator("art_c", "c_done"),
    ]
    op = _make_operator(reg, validators)
    op.run(MemoryStore(), _goal("a_done", "b_done", "c_done"))

    assert order == ["step_a", "step_b", "step_c"]
