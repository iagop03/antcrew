"""FlexibleHITL — callback-based human-in-the-loop approval gate."""
from __future__ import annotations

import logging
from dataclasses import dataclass
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
        replay_decisions: bool = False,
    ) -> None:
        self._callback = callback
        self._auto_approve = auto_approve
        self._checkpoints = set(checkpoints) if checkpoints else None
        self._history: list[tuple[str, HITLDecision]] = []
        self._trace_log = trace_log
        self._run_id = run_id
        self._replay_decisions = replay_decisions

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

        # Restart recovery: if replay_decisions is set and a TraceLog decision
        # already exists for this checkpoint, reuse it without calling the callback.
        _effective_run_id = run_id or self._run_id
        if self._replay_decisions and self._trace_log is not None and _effective_run_id:
            try:
                past = self._trace_log.get_hitl_decisions(_effective_run_id)
                for row in past:
                    if row.get("step") == checkpoint:
                        raw_action = row.get("decision", "reject")
                        try:
                            action = HITLAction(raw_action)
                        except ValueError:
                            action = HITLAction.REJECT
                        decision = HITLDecision(
                            action=action,
                            reason=row.get("reason", "replayed_from_tracelog"),
                            reviewer_id=row.get("reviewer_id", ""),
                        )
                        logger.info(
                            "HITL gate '%s' replayed cached decision '%s' for run %r",
                            checkpoint, raw_action, _effective_run_id,
                        )
                        self._history.append((checkpoint, decision))
                        if decision.action == HITLAction.MODIFY and decision.modified_state:
                            state.update(decision.modified_state)
                        return decision.approved
            except Exception as exc:
                logger.warning("HITL replay lookup failed: %r — proceeding to callback", exc)

        if self._auto_approve:
            # Use a system reviewer_id so auto-approves appear in TraceLog as auditable events,
            # not as gaps — "system:auto_approve" signals automation, not human absence.
            decision = HITLDecision(action=HITLAction.APPROVE, reason="auto", reviewer_id="system:auto_approve")
        elif self._callback is None:
            logger.error(
                "HITL gate '%s': no callback configured and auto_approve=False — defaulting to REJECT",
                checkpoint,
            )
            decision = HITLDecision(action=HITLAction.REJECT, reason="no_callback_configured")
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
        _effective_run_id = run_id or self._run_id  # noqa: F841 (already computed above for replay)
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
