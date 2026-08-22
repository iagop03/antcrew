"""EngineLoop: the engine's decision loop.

The EngineLoop never touches the ArtifactStore directly.
  - inspect()  reads ProjectState through Validators (pure observation).
  - decide()   selects an Executor from candidates (pure reasoning).
  - run()      orchestrates the loop and dispatches to Executors.

Decision policy in decide():
  1. Deterministic rules (cost-ordered by default).
  2. LLM fallback when rules produce no clear winner.
     Subclass EngineLoop and override decide() to inject LLM reasoning —
     the interface never exposes the model name or provider.

Escape conditions:
  STUCK        — no candidate can address the current gap.
  TIMEOUT      — max_iterations exceeded.
  INVALID_STATE — validators detected an inconsistent project state.
  NO_PROGRESS  — delta was empty for N consecutive iterations.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Callable

from .artifact import ArtifactId
from .capability import Executor
from .events import (
    CapabilityCompleted,
    CapabilityDispatched,
    ConditionSatisfied,
    EngineDecision,
    EngineError,
    EngineFinished,
    EngineStarted,
    EventLog,
    StateObserved,
)
from .goal import ConditionId, Goal
from .registry import CapabilityRegistry
from .selector import CapabilitySelector, CheapestFirst
from .state import ProjectState
from .store import ArtifactStore
from .validator import Validator


class EngineLoopError(Exception):
    class Kind(Enum):
        STUCK           = auto()
        TIMEOUT         = auto()
        INVALID_STATE   = auto()
        NO_PROGRESS     = auto()
        CANCELLED       = auto()
        BUDGET_EXCEEDED = auto()

    def __init__(self, kind: "EngineLoopError.Kind", message: str = "") -> None:
        self.kind = kind
        super().__init__(message or kind.name)


_NO_PROGRESS_LIMIT = 3
_UNLIMITED = 9999


class EngineLoop:
    def __init__(
        self,
        registry:       CapabilityRegistry,
        validators:     list[Validator],
        event_log:      EventLog,
        *,
        max_iterations: int = 50,
        selector:       CapabilitySelector | None = None,
        retry_limits:   dict[str, int] | None = None,
        total_limits:   dict[str, int] | None = None,
        stop_event:     threading.Event | None = None,
        max_cost_usd:   float | None = None,
        pre_dispatch:   "Callable[[Executor, ArtifactStore], bool] | None" = None,
    ) -> None:
        self._registry       = registry
        self._validators     = validators
        self._log            = event_log
        self._max_iterations = max_iterations
        self._selector       = selector or CheapestFirst()
        self._retry_limits   = retry_limits or {}
        self._total_limits   = total_limits or {}
        self._retry_counts:    dict[str, int] = {}
        self._dispatch_counts: dict[str, int] = {}
        self._total_cost_usd: float = 0.0
        self._stop_event     = stop_event
        self._max_cost_usd   = max_cost_usd
        self._pre_dispatch   = pre_dispatch

    # ------------------------------------------------------------------
    # inspect — pure observation
    # ------------------------------------------------------------------

    def inspect(
        self,
        store:            ArtifactStore,
        touched:          frozenset[ArtifactId] | None = None,
        cached_satisfied: frozenset[ConditionId]       = frozenset(),
    ) -> ProjectState:
        """Derive ProjectState by running Validators.  Never modifies anything.

        Incremental: when *touched* is given, only validators whose
        relevant_artifacts overlap with *touched* are re-run.  All others
        carry forward their last known result from *cached_satisfied*.
        On the first call (touched=None) every validator runs from scratch.
        """
        satisfied:    set[ConditionId] = set(cached_satisfied)
        observations: dict            = {}
        metrics:      dict            = {}

        for v in self._validators:
            if touched is not None and not v.global_scope:
                if not (v.relevant_artifacts & touched):
                    continue  # artifact unchanged — keep cached result
            result = v.validate(store)
            if result.satisfied:
                satisfied.add(result.condition_id)
            else:
                satisfied.discard(result.condition_id)  # condition may have become false
            observations.update(result.observations)
            metrics.update(result.metrics)

        is_invalid, reason = self._detect_invalid(observations)

        return ProjectState(
            satisfied      = frozenset(satisfied),
            observations   = observations,
            metrics        = metrics,
            timestamp      = datetime.now(UTC),
            is_invalid     = is_invalid,
            invalid_reason = reason,
        )

    # ------------------------------------------------------------------
    # decide — pure reasoning
    # ------------------------------------------------------------------

    def decide(
        self,
        candidates: list[Executor],
        state:      ProjectState,
        goal:       Goal,
    ) -> Executor | None:
        """Select the best executor from candidates via the configured CapabilitySelector.

        Candidates that have exceeded their retry_limit (consecutive stagnant runs)
        or total_limit (lifetime dispatches) are filtered out before selection.

        Override this method to add deterministic rules or LLM reasoning before
        or after delegating to the selector — without touching any other engine code.
        """
        eligible = [
            c for c in candidates
            if (self._retry_counts.get(c.descriptor.name, 0)
                < self._retry_limits.get(c.descriptor.name, _UNLIMITED))
            and (self._dispatch_counts.get(c.descriptor.name, 0)
                 < self._total_limits.get(c.descriptor.name, _UNLIMITED))
        ]
        chosen = self._selector.select(eligible, state, goal)
        if chosen is not None:
            self._log.emit(EngineDecision(
                chosen     = chosen.descriptor.name,
                candidates = tuple(ex.descriptor.name for ex in eligible),
                reason     = self._selector.name,
            ))
        return chosen

    # ------------------------------------------------------------------
    # run — orchestration loop
    # ------------------------------------------------------------------

    def run(self, store: ArtifactStore, goal: Goal) -> ProjectState:
        """Execute the observe → decide → dispatch loop until goal or error."""
        self._log.emit(EngineStarted(goal_description=goal.description))

        prev_satisfied:   frozenset[ConditionId] = frozenset()
        cached_satisfied: frozenset[ConditionId] = frozenset()
        touched:          frozenset[ArtifactId] | None = None
        no_progress_run:  int = 0
        # (capability_name, has_resets_retries_tag) — applied at top of next iteration
        pending_retry_update: tuple[str, bool] | None = None

        for iteration in range(self._max_iterations):
            # Check external cancellation signal before each iteration
            if self._stop_event is not None and self._stop_event.is_set():
                err = EngineLoopError(
                    EngineLoopError.Kind.CANCELLED,
                    "run cancelled by external request",
                )
                self._log.emit(EngineError(error_kind="CANCELLED", message=str(err)))
                self._log.emit(EngineFinished(
                    iterations=iteration,
                    success=False,
                    total_cost_usd=round(self._total_cost_usd, 6),
                ))
                raise err

            state = self.inspect(store, touched, cached_satisfied)
            cached_satisfied = state.satisfied
            self._log.emit(StateObserved(state=state, iteration=iteration))

            # -- emit newly satisfied conditions
            newly = state.satisfied - prev_satisfied
            for cid in newly:
                self._log.emit(ConditionSatisfied(condition_id=cid))
            prev_satisfied = state.satisfied

            # -- update retry counts now that we know if the previous capability made progress
            if pending_retry_update is not None:
                exec_name, is_resetter = pending_retry_update
                pending_retry_update = None
                if newly:
                    # real progress — give every capability a fresh start
                    self._retry_counts.clear()
                elif is_resetter:
                    # capability signals it reset context (e.g. BugFixer rewrote code);
                    # clear others' stagnation counts but preserve this cap's own tally
                    own = self._retry_counts.get(exec_name, 0)
                    self._retry_counts = {exec_name: own} if own else {}
                else:
                    self._retry_counts[exec_name] = (
                        self._retry_counts.get(exec_name, 0) + 1
                    )

            # -- escape: invalid state
            if state.is_invalid:
                err = EngineLoopError(
                    EngineLoopError.Kind.INVALID_STATE,
                    state.invalid_reason or "invalid project state detected",
                )
                self._log.emit(EngineError(
                    error_kind="INVALID_STATE",
                    message=str(err),
                ))
                raise err

            # -- escape: goal reached
            if state.satisfies(goal.desired_state):
                self._log.emit(EngineFinished(
                    iterations=iteration,
                    success=True,
                    total_cost_usd=round(self._total_cost_usd, 6),
                ))
                return state

            gap        = state.gap(goal.desired_state)
            candidates = self._registry.candidates_for(gap)
            # Enforce needs: only dispatch capabilities whose preconditions are met
            candidates = [c for c in candidates if c.descriptor.needs <= state.satisfied]
            executor   = self.decide(candidates, state, goal)

            # -- escape: stuck (includes retry/total-limit exhaustion)
            if executor is None:
                err = EngineLoopError(
                    EngineLoopError.Kind.STUCK,
                    _build_stuck_reason(
                        gap, candidates, state,
                        self._registry, self._retry_counts, self._retry_limits,
                        self._dispatch_counts, self._total_limits,
                    ),
                )
                self._log.emit(EngineError(error_kind="STUCK", message=str(err)))
                raise err

            name = executor.descriptor.name
            self._dispatch_counts[name] = self._dispatch_counts.get(name, 0) + 1

            self._log.emit(CapabilityDispatched(
                capability_name=name,
                gap=frozenset(gap),
            ))

            # Pre-dispatch gate — caller can inspect the executor and store,
            # then return False to abort the run cleanly (e.g. user prompt).
            if self._pre_dispatch is not None and not self._pre_dispatch(executor, store):
                err = EngineLoopError(
                    EngineLoopError.Kind.CANCELLED,
                    f"run cancelled before dispatching {name}",
                )
                self._log.emit(EngineError(error_kind="CANCELLED", message=str(err)))
                self._log.emit(EngineFinished(
                    iterations=iteration,
                    success=False,
                    total_cost_usd=round(self._total_cost_usd, 6),
                ))
                raise err

            # Inject event_log for streaming (BaseExecutor reads _event_log in _call())
            if hasattr(executor, "_event_log"):
                executor._event_log = self._log
            try:
                result = executor.execute(store, goal)
            finally:
                if hasattr(executor, "_event_log"):
                    executor._event_log = None
            self._total_cost_usd += result.cost_usd

            # Budget gate — checked immediately after cost is known
            if (
                self._max_cost_usd is not None
                and self._total_cost_usd >= self._max_cost_usd
            ):
                err = EngineLoopError(
                    EngineLoopError.Kind.BUDGET_EXCEEDED,
                    f"cost limit ${self._max_cost_usd:.4f} exceeded "
                    f"(spent ${self._total_cost_usd:.6f})",
                )
                self._log.emit(EngineError(error_kind="BUDGET_EXCEEDED", message=str(err)))
                self._log.emit(EngineFinished(
                    iterations=iteration,
                    success=False,
                    total_cost_usd=round(self._total_cost_usd, 6),
                ))
                raise err

            store.apply(result.delta)
            touched = result.delta.touched
            self._log.emit(CapabilityCompleted(
                capability_name=name,
                result=result,
            ))

            # schedule retry-count update for next iteration
            pending_retry_update = (name, "resets_retries" in executor.descriptor.tags)

            # -- escape: no progress
            if result.delta.is_empty():
                no_progress_run += 1
                if no_progress_run >= _NO_PROGRESS_LIMIT:
                    err = EngineLoopError(
                        EngineLoopError.Kind.NO_PROGRESS,
                        f"no delta produced for {_NO_PROGRESS_LIMIT} consecutive iterations",
                    )
                    self._log.emit(EngineError(error_kind="NO_PROGRESS", message=str(err)))
                    raise err
            else:
                no_progress_run = 0

        err = EngineLoopError(
            EngineLoopError.Kind.TIMEOUT,
            f"exceeded {self._max_iterations} iterations",
        )
        self._log.emit(EngineError(error_kind="TIMEOUT", message=str(err)))
        self._log.emit(EngineFinished(
            iterations=self._max_iterations,
            success=False,
            total_cost_usd=round(self._total_cost_usd, 6),
        ))
        raise err

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _detect_invalid(
        self, observations: dict
    ) -> tuple[bool, str | None]:
        """Override to detect inconsistent project states from observations."""
        return False, None


def _build_stuck_reason(
    gap, eligible_candidates, state,
    registry, retry_counts, retry_limits,
    dispatch_counts, total_limits,
) -> str:
    """Build a human-readable explanation of WHY the engine is stuck."""
    all_for_gap = registry.candidates_for(gap)
    if not all_for_gap:
        return (
            f"STUCK — gap {set(gap)}: no capability in the registry "
            "produces any of these conditions"
        )

    lines = [f"STUCK — gap {set(gap)}: {len(all_for_gap)} candidate(s), all blocked:"]
    for cap in all_for_gap:
        reasons = []
        # 1. Needs not met
        unmet = cap.descriptor.needs - state.satisfied
        if unmet:
            reasons.append(f"needs not satisfied: {set(unmet)}")
        # 2. Retry limit exhausted
        rc = retry_counts.get(cap.descriptor.name, 0)
        rl = retry_limits.get(cap.descriptor.name, _UNLIMITED)
        if rc >= rl < _UNLIMITED:
            reasons.append(f"retry limit exhausted ({rc}/{rl} consecutive stagnant runs)")
        # 3. Total limit exhausted
        dc = dispatch_counts.get(cap.descriptor.name, 0)
        dl = total_limits.get(cap.descriptor.name, _UNLIMITED)
        if dc >= dl < _UNLIMITED:
            reasons.append(f"total limit exhausted ({dc}/{dl} lifetime dispatches)")
        if not reasons:
            reasons.append("excluded by selector (unknown — check selector logic)")
        lines.append(f"  {cap.descriptor.name}: {'; '.join(reasons)}")
    return "\n".join(lines)
