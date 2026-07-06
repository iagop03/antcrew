"""Operator: the engine's decision loop.

The Operator never touches the ArtifactStore directly.
  - inspect()  reads ProjectState through Validators (pure observation).
  - decide()   selects an Executor from candidates (pure reasoning).
  - run()      orchestrates the loop and dispatches to Executors.

Decision policy in decide():
  1. Deterministic rules (cost-ordered by default).
  2. LLM fallback when rules produce no clear winner.
     Subclass Operator and override decide() to inject LLM reasoning —
     the interface never exposes the model name or provider.

Escape conditions:
  STUCK        — no candidate can address the current gap.
  TIMEOUT      — max_iterations exceeded.
  INVALID_STATE — validators detected an inconsistent project state.
  NO_PROGRESS  — delta was empty for N consecutive iterations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto

from .artifact import ArtifactId
from .capability import Executor, CapabilityResult
from .events import (
    EventLog, EngineStarted, EngineFinished, EngineError,
    StateObserved, CapabilityDispatched, CapabilityCompleted,
    ConditionSatisfied, OperatorDecision,
)
from .goal import ConditionId, Goal
from .registry import CapabilityRegistry
from .state import ProjectState
from .store import ArtifactStore
from .validator import Validator


class OperatorError(Exception):
    class Kind(Enum):
        STUCK         = auto()
        TIMEOUT       = auto()
        INVALID_STATE = auto()
        NO_PROGRESS   = auto()

    def __init__(self, kind: "OperatorError.Kind", message: str = "") -> None:
        self.kind = kind
        super().__init__(message or kind.name)


_NO_PROGRESS_LIMIT = 3


class Operator:
    def __init__(
        self,
        registry:       CapabilityRegistry,
        validators:     list[Validator],
        event_log:      EventLog,
        *,
        max_iterations: int = 50,
    ) -> None:
        self._registry       = registry
        self._validators     = validators
        self._log            = event_log
        self._max_iterations = max_iterations

    # ------------------------------------------------------------------
    # inspect — pure observation
    # ------------------------------------------------------------------

    def inspect(
        self,
        store:   ArtifactStore,
        touched: frozenset[ArtifactId] | None = None,
    ) -> ProjectState:
        """Derive ProjectState by running Validators.  Never modifies anything."""
        satisfied:    set[ConditionId] = set()
        observations: dict            = {}
        metrics:      dict            = {}

        for v in self._validators:
            if touched is not None and not v.global_scope:
                if not (v.relevant_artifacts & touched):
                    continue
            result = v.validate(store)
            if result.satisfied:
                satisfied.add(result.condition_id)
            observations.update(result.observations)
            metrics.update(result.metrics)

        is_invalid, reason = self._detect_invalid(observations)

        return ProjectState(
            satisfied      = frozenset(satisfied),
            observations   = observations,
            metrics        = metrics,
            timestamp      = datetime.utcnow(),
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
        """Select the best executor from candidates.

        Default policy: lowest cost.
        Override this method to inject deterministic rules or LLM reasoning
        without changing any other part of the engine.
        """
        if not candidates:
            return None
        chosen = min(candidates, key=lambda ex: ex.descriptor.cost)
        self._log.emit(OperatorDecision(
            chosen     = chosen.descriptor.name,
            candidates = tuple(ex.descriptor.name for ex in candidates),
            reason     = "lowest_cost",
        ))
        return chosen

    # ------------------------------------------------------------------
    # run — orchestration loop
    # ------------------------------------------------------------------

    def run(self, store: ArtifactStore, goal: Goal) -> ProjectState:
        """Execute the observe → decide → dispatch loop until goal or error."""
        self._log.emit(EngineStarted(goal_description=goal.description))

        prev_satisfied:  frozenset[ConditionId] = frozenset()
        touched:         frozenset[ArtifactId] | None = None
        no_progress_run: int = 0

        for iteration in range(self._max_iterations):
            state = self.inspect(store, touched)
            self._log.emit(StateObserved(state=state, iteration=iteration))

            # -- emit newly satisfied conditions
            newly = state.satisfied - prev_satisfied
            for cid in newly:
                self._log.emit(ConditionSatisfied(condition_id=cid))
            prev_satisfied = state.satisfied

            # -- escape: invalid state
            if state.is_invalid:
                err = OperatorError(
                    OperatorError.Kind.INVALID_STATE,
                    state.invalid_reason or "invalid project state detected",
                )
                self._log.emit(EngineError(
                    error_kind="INVALID_STATE",
                    message=str(err),
                ))
                raise err

            # -- escape: goal reached
            if state.satisfies(goal.desired_state):
                self._log.emit(EngineFinished(iterations=iteration, success=True))
                return state

            gap        = state.gap(goal.desired_state)
            candidates = self._registry.candidates_for(gap)
            executor   = self.decide(candidates, state, goal)

            # -- escape: stuck
            if executor is None:
                err = OperatorError(
                    OperatorError.Kind.STUCK,
                    f"no executor can address gap: {gap}",
                )
                self._log.emit(EngineError(error_kind="STUCK", message=str(err)))
                raise err

            self._log.emit(CapabilityDispatched(
                capability_name=executor.descriptor.name,
                gap=frozenset(gap),
            ))
            result  = executor.execute(store, goal)
            store.apply(result.delta)
            touched = result.delta.touched
            self._log.emit(CapabilityCompleted(
                capability_name=executor.descriptor.name,
                result=result,
            ))

            # -- escape: no progress
            if result.delta.is_empty():
                no_progress_run += 1
                if no_progress_run >= _NO_PROGRESS_LIMIT:
                    err = OperatorError(
                        OperatorError.Kind.NO_PROGRESS,
                        f"no delta produced for {_NO_PROGRESS_LIMIT} consecutive iterations",
                    )
                    self._log.emit(EngineError(error_kind="NO_PROGRESS", message=str(err)))
                    raise err
            else:
                no_progress_run = 0

        err = OperatorError(
            OperatorError.Kind.TIMEOUT,
            f"exceeded {self._max_iterations} iterations",
        )
        self._log.emit(EngineError(error_kind="TIMEOUT", message=str(err)))
        self._log.emit(EngineFinished(iterations=self._max_iterations, success=False))
        raise err

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _detect_invalid(
        self, observations: dict
    ) -> tuple[bool, str | None]:
        """Override to detect inconsistent project states from observations."""
        return False, None
