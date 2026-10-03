"""FlexibleHITL — callback-based human-in-the-loop approval gate."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

from antcrew.core.state import TeamState

if TYPE_CHECKING:
    from antcrew.trace import TraceLog

logger = logging.getLogger(__name__)


class HITLAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    REQUEST_CHANGES = "request_changes"
    SKIP = "skip"


@dataclass
class HITLDecision:
    action: HITLAction
    reason: str = ""
    modified_state: dict | None = None
    reviewer_id: str = ""

    @property
    def approved(self) -> bool:
        """True when execution should proceed past this gate."""
        return self.action in (HITLAction.APPROVE, HITLAction.MODIFY, HITLAction.SKIP)


ApprovalCallback = Callable[[str, TeamState], HITLDecision]


class FlexibleHITL:
    """Human-in-the-loop gate with pluggable approval callbacks.

    Usage::

        def my_reviewer(checkpoint: str, state: TeamState) -> HITLDecision:
            print(f"Review {checkpoint}: {state.get('prd')}")
            choice = input("Approve? [y/n]: ")
            return HITLDecision(
                action=HITLAction.APPROVE if choice == "y" else HITLAction.REJECT,
                reviewer_id="alice@example.com",
            )

        hitl = FlexibleHITL(callback=my_reviewer)

        # In a workflow:
        if not hitl.gate("prd_review", state):
            raise RuntimeError("PRD rejected by reviewer")
    """

    def __init__(
        self,
        callback: ApprovalCallback | None = None,
        *,
        auto_approve: bool = False,
        checkpoints: list[str] | None = None,
        trace_log: Optional["TraceLog"] = None,
        run_id: str = "",
    ) -> None:
        self._callback = callback
        self._auto_approve = auto_approve
        self._checkpoints = set(checkpoints) if checkpoints else None
        self._history: list[tuple[str, HITLDecision]] = []
        self._trace_log = trace_log
        self._run_id = run_id

    def register(self, callback: ApprovalCallback) -> None:
        """Replace the approval callback at runtime."""
        self._callback = callback

    def attach_trace(self, trace_log: "TraceLog", run_id: str) -> None:
        """Attach a TraceLog after construction (useful when run_id is not known at init)."""
        self._trace_log = trace_log
        self._run_id = run_id

    def gate(self, checkpoint: str, state: TeamState, *, run_id: str = "") -> bool:
        """Run the HITL gate for *checkpoint*.

        Returns True if execution should proceed, False to halt.
        Applies ``modified_state`` onto *state* in-place when action is MODIFY.

        Records the decision in the attached TraceLog (if any) under *run_id*
        (falls back to the run_id set at construction time).

        Security contract: if the callback raises any exception the decision
        defaults to REJECT, never APPROVE. Fail closed.
        """
        if self._checkpoints is not None and checkpoint not in self._checkpoints:
            return True

        if self._auto_approve or self._callback is None:
            decision = HITLDecision(action=HITLAction.APPROVE, reason="auto")
        else:
            try:
                decision = self._callback(checkpoint, state)
            except Exception as exc:
                logger.error(
                    "HITL callback raised %r at checkpoint '%s' — defaulting to REJECT",
                    exc, checkpoint,
                )
                decision = HITLDecision(
                    action=HITLAction.REJECT,
                    reason=f"callback_error: {exc}",
                )

        self._history.append((checkpoint, decision))

        if decision.action == HITLAction.MODIFY and decision.modified_state:
            state.update(decision.modified_state)

        # Persist to TraceLog when available
        _effective_run_id = run_id or self._run_id
        if self._trace_log is not None and _effective_run_id:
            try:
                self._trace_log.record_hitl(
                    run_id=_effective_run_id,
                    step=checkpoint,
                    decision=decision.action.value,
                    reviewer_id=decision.reviewer_id,
                    reason=decision.reason,
                )
            except Exception as exc:
                logger.warning("TraceLog.record_hitl failed: %r", exc)

        if not decision.approved:
            logger.info(
                "HITL gate '%s' %s (reviewer=%r): %s",
                checkpoint, decision.action.value.upper(),
                decision.reviewer_id or "unknown",
                decision.reason,
            )

        return decision.approved

    @property
    def history(self) -> list[tuple[str, HITLDecision]]:
        return list(self._history)

    def reset(self) -> None:
        self._history.clear()
